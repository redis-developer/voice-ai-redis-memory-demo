# AWS Configuration
variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-2"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "key_name" {
  description = "Name of the SSH key pair in AWS"
  type        = string
  default     = "bhavana"
}

# Application Configuration
variable "app_name" {
  description = "Application name for resource naming"
  type        = string
  default     = "voice-journal"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "repo_url" {
  description = "Git repository URL to deploy on the EC2 instance"
  type        = string
  default     = "https://github.com/bhavana-giri/voice_ai_redis_memory_demo.git"
}

variable "repo_branch" {
  description = "Git branch to deploy on the EC2 instance"
  type        = string
  default     = "main"
}

# App Secrets
variable "sarvam_api_key" {
  description = "Sarvam API key"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis connection URL"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
}

variable "ollama_url" {
  description = "Optional Ollama URL"
  type        = string
  default     = ""
}

variable "ollama_model" {
  description = "Optional Ollama model"
  type        = string
  default     = "llama3.2"
}

# Network Configuration
variable "allowed_ssh_cidr" {
  description = "CIDR block allowed for SSH access"
  type        = string
  default     = "0.0.0.0/0"
}
