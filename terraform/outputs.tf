output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "Public IP address of the EC2 instance"
  value       = aws_eip.app.public_ip
}

output "frontend_url" {
  description = "URL to access the frontend"
  value       = "http://${aws_eip.app.public_ip}:3000"
}

output "backend_url" {
  description = "URL to access the backend API"
  value       = "http://${aws_eip.app.public_ip}:8080"
}

output "memory_server_url" {
  description = "URL to access the Agent Memory Server"
  value       = "http://${aws_eip.app.public_ip}:8000"
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i <your-key.pem> ubuntu@${aws_eip.app.public_ip}"
}

output "vpc_id" {
  description = "VPC ID"
  value       = data.aws_vpc.default.id
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.app.id
}
