terraform {
  required_version = ">= 1.5.0"
  required_providers {
    huaweicloud = {
      source  = "registry.terraform.io/huaweicloud/huaweicloud"
      version = ">= 1.97.0"
    }
  }
}

provider "huaweicloud" {
  region = var.region
}

# 凭据管理服务：安全存储数据库凭据，由轮转函数维护其 CURRENT 版本
resource "huaweicloud_csms_secret" "db_credential" {
  name        = var.secret_name
  secret_type = "COMMON"
  description = "数据库默认管理员凭据（由轮转函数周期性维护）"
  secret_text = jsonencode({
    engine      = "postgresql"
    host        = var.db_host
    port        = var.db_port
    username    = var.db_user
    dbname      = var.db_name
    password    = var.db_initial_password
    instance_id = var.rds_instance_id
  })
  # 初始口令仅用于首次建密；轮转函数首次触发后会立即改写，且不在任何日志中回显明文
}

# 函数工作流：定时轮转函数
resource "huaweicloud_fgs_function" "rotate" {
  name        = "${var.project_name}-rotate"
  runtime     = "Python3.9"
  handler     = "rotate_secret.handler"
  memory_size = 256
  timeout     = 60
  code_type   = "zip"
  func_code   = filebase64("${path.module}/../src/functions/rotate_secret.zip")

  # 委托（agency）：函数运行时自动注入临时凭据到环境变量，无需在代码中硬编码密钥
  agency = var.function_agency

  # 非敏感的运行参数（凭据明文不在此处出现）
  user_data = jsonencode({
    SECRET_NAME     = var.secret_name
    RDS_INSTANCE_ID = var.rds_instance_id
    HW_REGION       = var.region
    HW_PROJECT_ID   = var.project_id
  })

  # 仅当数据库位于 VPC 内网、需函数直连时配置；留空则使用委托默认网络
  vpc_id     = var.function_vpc_id != "" ? var.function_vpc_id : null
  network_id = var.function_network_id != "" ? var.function_network_id : null
  dns_list   = var.function_dns_list != "" ? var.function_dns_list : null
}

# 定时触发器：按 CRON 周期驱动轮转（无需依赖外部调度服务）
resource "huaweicloud_fgs_trigger" "timer" {
  function_urn = huaweicloud_fgs_function.rotate.urn
  type         = "TIMER"
  timer {
    name          = "daily-rotation"
    schedule_type = "Cron"
    schedule      = var.rotation_cron
  }
}

output "secret_name" {
  value = huaweicloud_csms_secret.db_credential.name
}

output "function_urn" {
  value = huaweicloud_fgs_function.rotate.urn
}

output "trigger_id" {
  value = huaweicloud_fgs_trigger.timer.id
}
