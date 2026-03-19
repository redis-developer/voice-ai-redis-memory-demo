# Voice Journal with Redis Agent Memory Server

<p>
  <a href="https://github.com/redis/agent-memory-server"><img src="https://img.shields.io/badge/Redis-Agent%20Memory%20Server-red?logo=redis" alt="Redis Agent Memory Server"></a>
  <a href="https://sarvam.ai/"><img src="https://img.shields.io/badge/Sarvam-AI%20Voice-blue" alt="Sarvam AI"></a>
  <a href="https://nextjs.org/"><img src="https://img.shields.io/badge/Next.js-16-black" alt="Next.js"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-Backend-009688" alt="FastAPI"></a>
</p>

Voice-first journaling app that demonstrates how Redis Agent Memory Server can power long-term memory, session continuity, and semantic retrieval for a personal assistant that remembers what you said across conversations.

![Voice Journal App](assets/app-screenshot.png)

## Table of Contents

- [Demo Objectives](#demo-objectives)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Environment Configuration](#2-environment-configuration)
  - [3. Start Agent Memory Server](#3-start-agent-memory-server)
  - [4. Run with Docker](#4-run-with-docker)
- [5. Run for Development](#5-run-for-development)
- [Google Calendar Setup](#google-calendar-setup)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Usage](#usage)
- [Resources](#resources)

## Demo Objectives

- **Voice capture and transcription** with Sarvam AI for voice-first journaling
- **Long-term memory storage** with Redis Agent Memory Server for cross-session recall
- **Working memory continuity** by persisting session conversation turns
- **Intent-aware responses** using a RedisVL semantic router for log, chat, and calendar flows
- **Voice playback and summaries** using streaming TTS plus optional Ollama-backed response generation
- **Mood and schedule context** through mood logging and optional Google Calendar integration

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Memory** | [Redis Agent Memory Server](https://github.com/redis/agent-memory-server) | Long-term and working memory management |
| **Database** | [Redis Cloud](https://redis.io/cloud/) or Redis Stack | Journal storage, indexes, and vector-backed retrieval |
| **Voice** | [Sarvam AI](https://sarvam.ai/) | Speech-to-text and text-to-speech |
| **Backend** | [FastAPI](https://fastapi.tiangolo.com/) | API endpoints for journaling, chat, mood, and calendar |
| **Frontend** | [Next.js 16](https://nextjs.org/) + React 19 | Voice journal UI |
| **Intent Routing** | [RedisVL](https://github.com/redis/redis-vl-python) + OpenAI embeddings | Semantic intent detection |
| **Response Generation** | [Ollama](https://ollama.ai/) (optional) | Natural-language journal answers |
| **Deployment** | Docker Compose | Local containerized development and demos |

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose
- Redis instance reachable by the Agent Memory Server
- Sarvam AI API key
- OpenAI API key for embeddings and Agent Memory Server extraction
- Optional: Ollama if you want richer local journal responses
- Optional: Google Calendar OAuth credentials for schedule features

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/bhavana-giri/voice_ai_redis_memory_demo.git
cd voice_ai_redis_memory_demo
```

### 2. Environment Configuration

Copy the backend and deployment environment template:

```bash
cp .env.example .env
cp frontend/.env.local.example frontend/.env.local
```

Recommended minimum `.env` values:

```bash
SARVAM_API_KEY=your_sarvam_api_key_here
REDIS_URL=redis://default:password@your-redis-host:port
MEMORY_SERVER_URL=http://localhost:8000
OPENAI_API_KEY=sk-your_openai_api_key_here
NEXT_PUBLIC_API_URL=http://localhost:8080
CORS_ORIGINS=http://localhost:3000
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

Environment notes by runtime:

| Variable | Local development | Docker on localhost |
|----------|-------------------|---------------------|
| `MEMORY_SERVER_URL` | `http://localhost:8000` | `http://memory-server:8000` for the backend container |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8080` | `http://localhost:8080` |
| `CORS_ORIGINS` | `http://localhost:3000` | `http://localhost:3000` |

For local frontend development, keep `frontend/.env.local` aligned with the backend URL:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8080
```

### 3. Start Agent Memory Server

Start the memory server against your Redis instance:

```bash
docker run -p 8000:8000 \
  -e REDIS_URL=redis://default:<password>@<your-redis-host>:<port> \
  -e OPENAI_API_KEY=<your-openai-api-key> \
  redislabs/agent-memory-server:latest \
  agent-memory api --host 0.0.0.0 --port 8000 --task-backend=asyncio
```

### 4. Run with Docker

The repo now includes a Compose-based deployment path similar to the reference project:

```bash
docker compose up --build
```

Services:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8080
- Agent Memory Server: http://localhost:8000

Notes:

- The frontend image bakes in `NEXT_PUBLIC_API_URL` at build time.
- The backend container overrides `MEMORY_SERVER_URL` to the Compose service hostname.
- Ollama is not started by Compose; if it is unavailable, the backend falls back to simpler text responses.

### 5. Run for Development

**Backend**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

## Google Calendar Setup

Calendar support is optional and uses Google Calendar API OAuth, not an iCal URL.

1. Create OAuth desktop credentials in Google Cloud Console.
2. Save the downloaded file as `credentials.json` in the project root.
3. Start the backend and call a calendar route once.
4. Complete the browser-based OAuth consent flow to generate `token.json`.

If `credentials.json` or `token.json` is missing, the calendar API returns an empty list and the rest of the app still works.

## Architecture

```mermaid
flowchart LR
    USER[User Voice Input] --> FE[Next.js Frontend]
    FE --> BE[FastAPI Backend]
    BE --> STT[Sarvam STT]
    BE --> TTS[Sarvam TTS]
    BE --> AGENT[VoiceJournalAgent]
    AGENT --> ROUTER[RedisVL Intent Router]
    AGENT <--> AMS[Redis Agent Memory Server]
    AGENT --> REDIS[(Redis Journal Data)]
    AGENT --> OLLAMA[Ollama Optional]
    AGENT --> CAL[Google Calendar Optional]
    TTS --> FE
```

### Runtime Flow

1. The frontend captures typed or recorded input and sends it to FastAPI.
2. The backend transcribes audio with Sarvam when needed.
3. The semantic router decides whether the request is a journal log, a journal recall query, or a calendar question.
4. The agent reads working memory and long-term memory from Redis Agent Memory Server.
5. The response is generated with Ollama when available, or with a simple fallback when it is not.
6. The backend streams TTS audio back to the frontend for playback.

## Project Structure

```text
voice_ai_redis_memory_demo/
├── api/
│   └── main.py
├── src/
│   ├── analytics.py
│   ├── audio_handler.py
│   ├── calendar_client.py
│   ├── intent_router.py
│   ├── journal_manager.py
│   ├── journal_store.py
│   ├── memory_client.py
│   └── voice_agent.py
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   └── types/
│   ├── .env.local.example
│   └── package.json
├── docker/
│   ├── Dockerfile.backend
│   └── Dockerfile.frontend
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Usage

1. Record a voice journal entry from the modal, or type directly in chat.
2. Save a mood snapshot from the dashboard header.
3. Ask questions such as "What did I say about work this week?" or "Do I have meetings today?"
4. Reuse the same chat session to benefit from working memory continuity.
5. Review the sidebar schedule and journal feed in the frontend.

## Resources

- [Redis Agent Memory Server](https://github.com/redis/agent-memory-server)
- [Sarvam AI Docs](https://docs.sarvam.ai/)
- [RedisVL](https://redis.io/docs/latest/develop/ai/redisvl/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Next.js](https://nextjs.org/docs)
