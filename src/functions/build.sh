#!/usr/bin/env bash
# 构建函数部署包 rotate_secret.zip
#
# 说明：
#   - 依赖均为纯 Python 包，macOS / Linux 构建产物可通用；
#     若运行环境对原生扩展敏感，建议在 Linux x86_64 上执行本脚本。
#   - 产物 rotate_secret.zip 由 terraform 的 func_code 引用。
set -euo pipefail

cd "$(dirname "$0")"

PKG_DIR="pkg"
ZIP_NAME="rotate_secret.zip"

rm -rf "${PKG_DIR}" "${ZIP_NAME}"

echo "==> 安装依赖到 ${PKG_DIR}"
python3 -m pip install -r requirements.txt -t "${PKG_DIR}" --quiet

echo "==> 拷贝函数代码"
cp rotate_secret.py "${PKG_DIR}/"

echo "==> 打包 ${ZIP_NAME}"
cd "${PKG_DIR}"
zip -r "../${ZIP_NAME}" . --quiet
cd ..

echo "==> 构建完成：$(du -h "${ZIP_NAME}" | cut -f1)  ${ZIP_NAME}"
