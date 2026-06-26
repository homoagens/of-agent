#!/bin/bash
cd "$(dirname "$0")"

echo ""
echo "========================================"
echo "  OF-Agent - LLM configuration"
echo "========================================"
echo ""
echo "This will write a .env file for OF-Agent."
echo "Point it at any OpenAI-compatible endpoint"
echo "(Ollama, LM Studio, vLLM, llama.cpp, OpenAI, Groq, OpenRouter...)."
echo ""

read -p "Backend URL [http://localhost:11434]: " BASE_URL
BASE_URL=${BASE_URL:-http://localhost:11434}

read -p "Model name [llama3.1:8b]: " MODEL
MODEL=${MODEL:-llama3.1:8b}

read -p "API key [local]: " API_KEY
API_KEY=${API_KEY:-local}

read -p "Temperature [0.2]: " TEMP
TEMP=${TEMP:-0.2}

if [ -f .env ]; then
    cp .env .env.backup
    echo ""
    echo "Existing .env backed up to .env.backup"
fi

cat > .env <<EOF
OF_AGENT_BACKEND_URL=${BASE_URL}
OF_AGENT_BACKEND_KEY=${API_KEY}
OF_AGENT_MODEL=${MODEL}
OF_AGENT_TEMPERATURE=${TEMP}
EOF

echo ""
echo "Configuration saved to .env:"
echo "  backend URL:  ${BASE_URL}"
echo "  model:        ${MODEL}"
echo "  temperature:  ${TEMP}"
echo ""
