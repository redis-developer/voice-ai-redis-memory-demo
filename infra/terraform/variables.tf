variable "aws_region" {
  description = "AWS region to deploy App Runner services into."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Base name used for AWS resources."
  type        = string
  default     = "voice-journal"
}

variable "environment" {
  description = "Environment label added to AWS resources."
  type        = string
  default     = "dev"
}

variable "image_tag" {
  description = "Container image tag to deploy for frontend, backend, and memory server."
  type        = string
  default     = "latest"
}

variable "sarvam_api_key" {
  description = "Sarvam API key for speech services."
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key used by the memory server and semantic router."
  type        = string
  sensitive   = true
}

variable "google_client_id" {
  description = "Google OAuth client ID used for browser sign-in and backend token verification."
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis connection URL for the memory server and backend."
  type        = string
  sensitive   = true
}

variable "memory_server_generation_model" {
  description = "Model used by Redis Agent Memory Server."
  type        = string
  default     = "gpt-4o-mini"
}

variable "memory_server_embedding_model" {
  description = "Embedding model used by Redis Agent Memory Server."
  type        = string
  default     = "text-embedding-3-large"
}

variable "memory_server_vector_dimensions" {
  description = "Vector dimensions used by RedisVL for the memory index."
  type        = number
  default     = 3072
}

variable "backend_cors_origins" {
  description = "Comma-separated origins allowed by FastAPI CORS. Use * for lightweight testing."
  type        = string
  default     = "*"
}

variable "openai_chat_model" {
  description = "OpenAI model used by the backend for response generation."
  type        = string
  default     = "gpt-4o-mini"
}
