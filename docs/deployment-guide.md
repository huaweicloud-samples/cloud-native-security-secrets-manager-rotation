# 部署指南

本文档提供从零部署数据库凭据定时轮转套件的可复现步骤。所有命令均假定在仓库根目录执行。

## 1. 准备环境

- 安装 Terraform `>= 1.5.0`；
- 安装 Python `>= 3.9`；
- 在目标区域创建关系型数据库实例，记录实例 ID、连接地址、端口、默认管理员用户名；
- 创建具备「凭据管理服务」与「关系型数据库服务」权限的委托（agency），记录其 URN；
- 配置访问目标区域的命令行凭证（环境变量或配置文件，请勿写入仓库）。

## 2. 构建函数包

```bash
cd src/functions
bash build.sh
cd ../../
```

脚本会安装依赖到 `pkg/` 并打包为 `src/functions/rotate_secret.zip`。该压缩包由 Terraform 的 `func_code` 引用。

## 3. 配置变量

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

编辑 `terraform.tfvars`，至少填写：

| 变量 | 说明 |
|---|---|
| `project_id` | 项目 ID |
| `rds_instance_id` | 数据库实例 ID |
| `db_host` | 数据库地址 |
| `db_port` | 数据库端口（PostgreSQL 默认 5432） |
| `db_user` | 默认管理员用户名 |
| `db_name` | 连接验证使用的库名 |
| `db_initial_password` | 首次建密初始口令（轮转后随即改写） |
| `function_agency` | 函数委托 URN |

内网直连场景另填 `function_vpc_id` / `function_network_id` / `function_dns_list`；公网场景保持为空。

## 4. 初始化与校验

```bash
terraform init
terraform validate
```

预期输出：`Success! The configuration is valid.`

## 5. 部署

```bash
terraform apply
```

确认后输出三个值：

- `secret_name`：凭据名称（`rds-db-credential`）；
- `function_urn`：轮转函数 URN；
- `trigger_id`：定时触发器 ID。

此时凭据管理服务中已存在初始凭据，函数与定时触发器均已就绪。

## 6. 验证（部署后实测）

> 本章对应部署后验证要求：完成部署后必须实际确认轮转链路打通。

### 6.1 等待或手动触发

定时触发器默认每日 `02:00` 触发。如需立即验证，可在函数工作流控制台手动触发函数 `db-credential-rotation-rotate`。

### 6.2 核对凭据版本

在凭据管理服务控制台查看 `rds-db-credential`，确认其 CURRENT 版本的 `password` 字段已被更新为轮转后的新值。

### 6.3 连通性验证脚本

以下脚本读取凭据管理服务中的 CURRENT 版本并用其直连数据库执行 `SELECT 1`，可作为独立验证手段（运行环境需具备相应临时凭据）：

```python
import os, json
import pg8000.native
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.region.region import Region
from huaweicloudsdkcsms.v1 import CsmsClient, ShowSecretRequest

ak = os.environ["HUAWEICLOUD_ACCESS_KEY"]
sk = os.environ["HUAWEICLOUD_SECRET_KEY"]
pid = os.environ["HUAWEICLOUD_PROJECT_ID"]
region = os.environ.get("HUAWEICLOUD_REGION", "cn-north-4")

client = (CsmsClient.new_builder()
          .with_credentials(BasicCredentials(ak, sk, pid))
          .with_region(Region(region, "csms.%s.myhuaweicloud.com" % region))
          .build())
secret = client.show_secret(ShowSecretRequest(secret_name="rds-db-credential")).secret
cred = json.loads(secret.secret_string)

conn = pg8000.native.Connection(
    host=cred["host"], port=int(cred["port"]),
    user=cred["username"], password=cred["password"],
    database=cred["dbname"], ssl_context=True)
conn.run("SELECT 1")
conn.close()
print("凭据中 CURRENT 版本口令可正常连接数据库")
```

若输出「凭据中 CURRENT 版本口令可正常连接数据库」，则轮转链路验证通过。

### 6.4 查看函数日志

在函数工作流控制台查看 `db-credential-rotation-rotate` 的运行日志，确认出现「轮转成功」且无异常。

## 7. 销毁

```bash
cd infra
terraform destroy
```

执行后凭据、函数、触发器均被释放。
