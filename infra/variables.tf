variable "region" {
  type        = string
  default     = "cn-north-4"
  description = "部署区域（不含任何外部平台标识）"
}

variable "project_name" {
  type        = string
  default     = "db-credential-rotation"
  description = "资源命名前缀（不含任何外部平台标识）"
}

variable "project_id" {
  type        = string
  description = "账号项目 ID，用于函数内临时凭据构造"
}

variable "secret_name" {
  type        = string
  default     = "rds-db-credential"
  description = "凭据管理服务中存储数据库凭据的凭据名称"
}

variable "rds_instance_id" {
  type        = string
  description = "目标关系型数据库实例 ID（轮转其默认管理员口令）"
}

variable "db_host" {
  type        = string
  description = "数据库实例内网/公网连接地址"
}

variable "db_port" {
  type        = number
  default     = 5432
  description = "数据库监听端口"
}

variable "db_user" {
  type        = string
  default     = "root"
  description = "数据库默认管理员用户名（与实例类型匹配）"
}

variable "db_name" {
  type        = string
  default     = "postgres"
  description = "连接验证使用的数据库名"
}

variable "db_initial_password" {
  type        = string
  sensitive   = true
  description = "首次建密使用的初始口令；轮转函数会在首次触发后立即改写，不回显明文"
}

variable "function_agency" {
  type        = string
  description = "函数委托（agency）URN，需具备凭据管理服务与关系型数据库服务权限"
}

variable "rotation_cron" {
  type        = string
  default     = "0 0 2 * * ?"
  description = "定时触发器 CRON 表达式（默认每日 02:00 触发一次轮转）"
}

# 以下三项仅当数据库位于 VPC 内网、函数需直连时填写；留空则函数为公网/委托默认网络
variable "function_vpc_id" {
  type        = string
  default     = ""
  description = "函数接入的 VPC ID（内网直连数据库时必填）"
}

variable "function_network_id" {
  type        = string
  default     = ""
  description = "函数接入的子网 ID（内网直连数据库时必填）"
}

variable "function_dns_list" {
  type        = string
  default     = ""
  description = "函数 VPC 内网 DNS 列表（内网直连数据库时必填，逗号分隔）"
}
