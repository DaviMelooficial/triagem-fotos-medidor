# Atalhos do projeto. Rode "just" sem argumentos para ver a lista.
# Modelo padrão: o mesmo default do extrator.py; dá para trocar com OLLAMA_MODELO=...
modelo_padrao := env_var_or_default("OLLAMA_MODELO", "qwen3-vl:4b")

# Lista as receitas
default:
    @just --list

# Cria o .venv e instala as versões fixadas no uv.lock
setup:
    uv sync

# Baixa o modelo de visão no Ollama (~3 GB, só na primeira vez)
modelo:
    ollama pull {{modelo_padrao}}

# Sobe a API em http://localhost:3000 (Swagger na mesma página); porta ocupada? "just serve 3001"
serve porta="3000":
    uv run bentoml serve service:TriagemMedidor --port {{porta}}

# Roda todos os testes (precisa do Ollama aberto e do modelo baixado)
testar:
    uv run pytest -v

# Envia uma foto de exemplo para a API já rodando (abra "just serve" em outro terminal)
demo foto="exemplos/01_eletronico.jpg" porta="3000":
    curl -s -X POST http://localhost:{{porta}}/extrair -F "foto=@{{foto}}"

# Redesenha as imagens sintéticas de exemplos/
exemplos:
    uv run python gerar_exemplos.py

# Mede o acerto num lote real local (pasta de fotos + CSV), que não vai para o git
avaliar PASTA CSV N="50":
    uv run python avaliar.py --pasta "{{PASTA}}" --csv "{{CSV}}" --n {{N}} --modelo {{modelo_padrao}}

# Falha se algum arquivo versionado citar o cliente/região ou se houver foto fora de exemplos/
privacidade:
    #!/usr/bin/env bash
    set -uo pipefail
    # As letras entre colchetes evitam que este próprio arquivo contenha as palavras proibidas.
    achados=$(git ls-files -z | xargs -0 grep -inE 'n[e]oenergia|c[e]lpe|p[e]rnambuco|c[e]sar|e[l]ster' | grep -viE '@c[e]sar\.school')
    fotos=$(git ls-files | grep -iE '\.(jpe?g|png)$' | grep -v '^exemplos/')
    if [ -n "$achados" ] || [ -n "$fotos" ]; then
        echo "FALHOU: conteúdo proibido em arquivos versionados:"
        echo "$achados"
        echo "$fotos"
        exit 1
    fi
    echo "OK: nenhum termo proibido nem foto fora de exemplos/"
