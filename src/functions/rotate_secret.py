# 数据库凭据定时轮转函数（函数工作流 + 凭据管理服务）
#
# 设计目标：
#   通过函数工作流定时触发器驱动，周期性地将关系型数据库实例的默认管理员口令
#   自动轮转，并把最新口令安全存入凭据管理服务（DEW / CSMS），全程不落盘、不回显明文。
#
# 工作流程（单次触发内完成「生成 → 改密 → 验证 → 落库」四步）：
#   1. 从凭据管理服务读取当前凭据（host / 端口 / 库名 / 用户名 / 口令）
#   2. 生成高强度新口令
#   3. 调用 RDS 服务接口重置实例默认管理员口令
#   4. 用新口令直连数据库做连通性验证
#   5. 将新口令作为新版凭据写入凭据管理服务（标记为 CURRENT 版本）
#
# 凭据获取（安全）：函数配置委托（agency）后，运行时会自动注入临时凭据到环境变量，
#   本函数仅读取运行时提供的临时凭据，源码不保留任何长期密钥。

import json
import logging
import os
import secrets
import string

import pg8000.native

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkcore.region.region import Region
from huaweicloudsdkcsms.v1 import (
    CsmsClient,
    ShowSecretRequest,
    ShowSecretVersionRequest,
    CreateSecretVersionRequest,
    CreateSecretVersionRequestBody,
)
from huaweicloudsdkrds.v3 import (
    RdsClient,
    ResetPwdRequest,
    PwdResetRequest,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 口令字符集：大写 / 小写 / 数字 + 安全特殊字符（排除 " ' \ : 等易引发解析问题的字符）
_UPPER = string.ascii_uppercase
_LOWER = string.ascii_lowercase
_DIGIT = string.digits
_SPECIAL = "!@#$%^*-_=+"
_ALL = _UPPER + _LOWER + _DIGIT + _SPECIAL


def _get_env_credential():
    """读取运行环境注入的临时凭据（函数委托 agency 自动注入）。

    返回 (ak, sk, project_id, region, security_token)。
    """
    ak = os.environ.get("HUAWEICLOUD_ACCESS_KEY") or os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HUAWEICLOUD_SECRET_KEY") or os.environ.get("HW_SECRET_KEY")
    token = os.environ.get("HUAWEICLOUD_SECURITY_TOKEN") or os.environ.get("HW_SECURITY_TOKEN")
    project_id = (
        os.environ.get("HUAWEICLOUD_PROJECT_ID")
        or os.environ.get("HW_PROJECT_ID")
        or os.environ.get("OS_PROJECT_ID")
    )
    region = (
        os.environ.get("HUAWEICLOUD_REGION")
        or os.environ.get("HW_REGION")
        or os.environ.get("OS_REGION_NAME")
        or "cn-north-4"
    )
    if not (ak and sk and project_id):
        raise RuntimeError(
            "未检测到运行环境凭据（HUAWEICLOUD_ACCESS_KEY / HUAWEICLOUD_SECRET_KEY / "
            "HUAWEICLOUD_PROJECT_ID）。请为函数配置具备 CSMS 与 RDS 权限的委托（agency）。"
        )
    return ak, sk, project_id, region, token


def _build_csms_client(region, ak, sk, project_id, token):
    creds = BasicCredentials(ak, sk, project_id, token) if token else BasicCredentials(ak, sk, project_id)
    return (
        CsmsClient.new_builder()
        .with_credentials(creds)
        # CSMS（凭据管理服务）与 KMS 共用 DEW 网关域名。
        # 原写法使用 csms.<region>.myhuaweicloud.com，该域名不存在（DNS 无法解析），
        # 会导致所有 CSMS 调用 HostUnreachable，必须改为 kms.<region>.myhuaweicloud.com。
        .with_region(Region(region, "kms.%s.myhuaweicloud.com" % region))
        .build()
    )


def _build_rds_client(region, ak, sk, project_id, token):
    creds = BasicCredentials(ak, sk, project_id, token) if token else BasicCredentials(ak, sk, project_id)
    return (
        RdsClient.new_builder()
        .with_credentials(creds)
        .with_region(Region(region, "rds.%s.myhuaweicloud.com" % region))
        .build()
    )


def _generate_password(length=32):
    """生成满足关系型数据库口令复杂度要求的高强度口令。"""
    length = max(12, int(length))
    # 保证至少包含大写、小写、数字、特殊字符各一个
    pwd = [
        secrets.choice(_UPPER),
        secrets.choice(_LOWER),
        secrets.choice(_DIGIT),
        secrets.choice(_SPECIAL),
    ]
    pwd += [secrets.choice(_ALL) for _ in range(length - len(pwd))]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)


def _read_current_credential(csms_client, secret_name):
    """读取凭据管理服务中当前 CURRENT 版本的凭据 JSON。"""
    resp = csms_client.show_secret(ShowSecretRequest(secret_name=secret_name))
    secret = getattr(resp, "secret", None)
    secret_string = getattr(secret, "secret_string", None) or getattr(secret, "secret_binary", None)
    # 注意：show_secret 返回的是凭据「元数据」（Secret），不含明文；
    # 明文由 show_secret_version 返回（Version.secret_string）。此处为兼容兜底：
    # 若元数据未携带明文，则读取最新版本内容。
    if not secret_string:
        resp = csms_client.show_secret_version(
            ShowSecretVersionRequest(secret_name=secret_name, version_id="latest")
        )
        version = getattr(resp, "version", None)
        secret_string = getattr(version, "secret_string", None) or getattr(
            version, "secret_binary", None
        )
    if not secret_string:
        raise RuntimeError("凭据 %s 未包含可解析的明文内容" % secret_name)
    try:
        data = json.loads(secret_string)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("凭据 %s 的明文不是合法 JSON：%s" % (secret_name, exc))
    for field in ("host", "username", "password"):
        if field not in data:
            raise KeyError("凭据 %s 缺少必填字段 '%s'" % (secret_name, field))
    data.setdefault("port", 5432)
    data.setdefault("dbname", "postgres")
    return data


def _rotate_rds_password(rds_client, instance_id, new_password):
    """调用 RDS 服务重置实例默认管理员口令。"""
    rds_client.reset_pwd(
        ResetPwdRequest(instance_id=instance_id, body=PwdResetRequest(db_user_pwd=new_password))
    )


def _test_db_login(cred, new_password, use_ssl=True):
    """用新口令直连数据库验证轮转结果。返回 True/False。"""
    try:
        conn = pg8000.native.Connection(
            host=cred["host"],
            port=int(cred["port"]),
            user=cred["username"],
            password=new_password,
            database=cred["dbname"],
            ssl_context=True if use_ssl else False,
        )
        conn.run("SELECT 1")
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - 验证失败需返回 False，不抛异常
        logger.warning("新口令连通性验证失败：%s", exc)
        return False


def _write_new_credential(csms_client, secret_name, cred, new_password):
    """将新口令作为 CURRENT 版本写入凭据管理服务。"""
    cred = dict(cred)
    cred["password"] = new_password
    # 阶段名必须使用平台预置的系统阶段 SYSCURRENT（原先使用的 "CURRENT" 不是系统保留阶段，
    # 会生成一个游离的新版本，而订阅方读取 latest 时仍拿到旧口令 —— 轮转后业务取到的旧口令已失效）。
    csms_client.create_secret_version(
        CreateSecretVersionRequest(
            secret_name=secret_name,
            body=CreateSecretVersionRequestBody(
                secret_string=json.dumps(cred, ensure_ascii=False),
                version_stages=["SYSCURRENT"],
            ),
        )
    )


def rotate(secret_name, instance_id=None):
    """执行一次完整轮转。可被 handler 或测试直接调用。"""
    ak, sk, project_id, region, token = _get_env_credential()
    csms_client = _build_csms_client(region, ak, sk, project_id, token)
    rds_client = _build_rds_client(region, ak, sk, project_id, token)

    cred = _read_current_credential(csms_client, secret_name)
    # 实例 ID 优先取环境变量显式配置，否则取凭据内记录的 instance_id
    target_instance = instance_id or os.environ.get("RDS_INSTANCE_ID") or cred.get("instance_id")
    if not target_instance:
        raise ValueError("缺少 RDS 实例 ID（请通过环境变量 RDS_INSTANCE_ID 或凭据字段 instance_id 提供）")

    new_password = _generate_password(os.environ.get("PASSWORD_LENGTH", "32"))
    _rotate_rds_password(rds_client, target_instance, new_password)

    # 验证：优先尝试 SSL，失败回退非 SSL
    if not _test_db_login(cred, new_password, use_ssl=True):
        if not _test_db_login(cred, new_password, use_ssl=False):
            raise RuntimeError("新口令已下发至数据库，但直连验证失败，请检查网络连通性与安全组")

    _write_new_credential(csms_client, secret_name, cred, new_password)
    logger.info("凭据 %s 轮转成功，新口令已写入凭据管理服务（CURRENT 版本）", secret_name)
    return {"secret_name": secret_name, "status": "rotated"}


def handler(event, context):
    """函数工作流入口（定时触发器每次触发即执行一次轮转）。"""
    secret_name = os.environ.get("SECRET_NAME")
    if not secret_name:
        raise ValueError("环境变量 SECRET_NAME 未配置")
    instance_id = os.environ.get("RDS_INSTANCE_ID")
    return rotate(secret_name, instance_id)
