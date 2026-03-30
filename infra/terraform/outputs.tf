output "frontend_ecr_repository_url" {
  description = "ECR repository URL for the frontend image."
  value       = aws_ecr_repository.frontend.repository_url
}

output "backend_ecr_repository_url" {
  description = "ECR repository URL for the backend image."
  value       = aws_ecr_repository.backend.repository_url
}

output "memory_server_ecr_repository_url" {
  description = "ECR repository URL for the mirrored memory server image."
  value       = aws_ecr_repository.memory_server.repository_url
}

output "memory_server_url" {
  description = "Public App Runner URL for the memory server."
  value       = "https://${aws_apprunner_service.memory_server.service_url}"
}

output "backend_url" {
  description = "Public App Runner URL for the backend API."
  value       = "https://${aws_apprunner_service.backend.service_url}"
}

output "frontend_url" {
  description = "Public App Runner URL for the frontend."
  value       = "https://${aws_apprunner_service.frontend.service_url}"
}
