locals {
  service_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${local.service_prefix}/frontend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "backend" {
  name                 = "${local.service_prefix}/backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "memory_server" {
  name                 = "${local.service_prefix}/memory-server"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name = "${local.service_prefix}-apprunner-ecr-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "build.apprunner.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr_access" {
  role       = aws_iam_role.apprunner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_apprunner_service" "memory_server" {
  service_name = "${local.service_prefix}-memory-server"

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${aws_ecr_repository.memory_server.repository_url}:${var.image_tag}"

      image_configuration {
        port          = "8000"
        start_command = "agent-memory api --host 0.0.0.0 --port 8000 --task-backend asyncio"

        runtime_environment_variables = {
          OPENAI_API_KEY            = var.openai_api_key
          REDIS_URL                 = var.redis_url
          GENERATION_MODEL          = var.memory_server_generation_model
          EMBEDDING_MODEL           = var.memory_server_embedding_model
          REDISVL_VECTOR_DIMENSIONS = tostring(var.memory_server_vector_dimensions)
        }
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/openapi.json"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }
}

resource "aws_apprunner_service" "backend" {
  service_name = "${local.service_prefix}-backend"

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"

      image_configuration {
        port = "8080"

        runtime_environment_variables = {
          SARVAM_API_KEY    = var.sarvam_api_key
          OPENAI_API_KEY    = var.openai_api_key
          OPENAI_CHAT_MODEL = var.openai_chat_model
          REDIS_URL         = var.redis_url
          GOOGLE_CLIENT_ID  = var.google_client_id
          MEMORY_SERVER_URL = "https://${aws_apprunner_service.memory_server.service_url}"
          CORS_ORIGINS      = var.backend_cors_origins
        }
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  depends_on = [aws_apprunner_service.memory_server]
}

resource "aws_apprunner_service" "frontend" {
  service_name = "${local.service_prefix}-frontend"

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${aws_ecr_repository.frontend.repository_url}:${var.image_tag}"

      image_configuration {
        port = "3000"
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  depends_on = [aws_apprunner_service.backend]
}
