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

# Faz tudo de uma vez: instala dependências, baixa o modelo e sobe a API
tudo porta="3000": setup modelo (serve porta)

# Sobe a API em http://localhost:3000 (Swagger na mesma página); porta ocupada? "just serve 3001"
serve porta="3000":
    uv run bentoml serve service:TriagemMedidor --port {{porta}}

# Roda todos os testes (precisa do Ollama aberto e do modelo baixado)
testar:
    uv run pytest -v

# Roda só os testes que não precisam do Ollama (monitoramento no modo simulado, KS/PSI, votação)
testar-monitoramento:
    uv run pytest -v tests/test_monitoramento.py tests/test_drift.py tests/test_votacao.py

# Envia uma foto de exemplo para a API já rodando (abra "just serve" em outro terminal)
demo foto="exemplos/01_display_registro_103.jpg" porta="3000":
    @curl -s -X POST http://localhost:{{porta}}/extrair -F "foto=@{{foto}}"

# Mede o acerto num lote real local (pasta de fotos + CSV), que não vai para o git
avaliar PASTA CSV N="50":
    uv run python avaliar.py --pasta "{{PASTA}}" --csv "{{CSV}}" --n {{N}} --modelo {{modelo_padrao}}

# --- Monitoramento (detalhes em monitoring/README.md) ---

# Sobe a API no modo simulado: mesmo fluxo e mesmo log, sem Ollama (para gerar carga numa máquina sem GPU)
serve-simulado porta="3000":
    BACKEND=simulado uv run bentoml serve service:TriagemMedidor --port {{porta}}

# Manda N pedidos ao /extrair (perfil: misto, normal ou drift); a API precisa estar rodando
carga n="100" perfil="misto" porta="3000":
    uv run python monitoring/gerar_carga.py --n {{n}} --perfil {{perfil}} --url http://localhost:{{porta}}

# Mostra o log crescendo, uma linha por evento (Ctrl+C para sair)
rastro:
    tail -n 5 -f logs/inferencia.jsonl

# Contagem de eventos no log, por evento e status
rastro-resumo porta="3000":
    @curl -s "http://localhost:{{porta}}/monitor/resumo" | uv run python -c "import json,sys; d=json.load(sys.stdin); print('respostas:', d['n'], d['por_status'])"

# Linhas do /metrics com as métricas do /extrair e as nossas (triagem_*)
metricas porta="3000":
    @curl -s http://localhost:{{porta}}/metrics | grep -E '^(bentoml_service_request_(total|duration_seconds_count)|triagem_predicoes_total)' | grep -v -e readyz -e livez -e '"/metrics"'

# Recalcula monitoring/referencia.json (conjunto de referência para KS/PSI)
referencia:
    uv run python monitoring/referencia.py

# KS, PSI, histogramas e boxplots do log contra a referência (gráficos em monitoring/analise/)
analise rotulo="carga" *filtros="":
    uv run python monitoring/analise.py --rotulo {{rotulo}} {{filtros}}

# Sobe Prometheus, Loki, Promtail e Grafana (Docker); a API roda fora, na porta indicada
pilha porta="3000":
    mkdir -p logs
    PORTA_API={{porta}} docker compose -f monitoring/docker-compose.yml up -d

# Derruba a pilha (os dados ficam nos volumes; para apagar também: docker compose -f monitoring/docker-compose.yml down -v)
pilha-parar:
    docker compose -f monitoring/docker-compose.yml down

# Copia o /metrics para o DuckDB (monitoring/metrics.duckdb) N vezes, a cada 5 s, e carrega o log na tabela eventos
duckdb coletas="12" porta="3000":
    uv run python monitoring/metrics_to_duckdb.py --coletas {{coletas}} --intervalo 5 --url http://localhost:{{porta}} --log

# Roda as consultas SQL de exemplo (monitoring/consultas.sql) no DuckDB
duckdb-consultas:
    uv run python monitoring/metrics_to_duckdb.py --consultas

# Falha se algum arquivo versionado citar o cliente/região ou se houver foto fora de exemplos/
privacidade:
    #!/usr/bin/env bash
    set -uo pipefail
    # As letras entre colchetes evitam que este próprio arquivo contenha as palavras proibidas.
    termos='n[e]oenergia|c[e]lpe|p[e]rnambuco|c[e]sar|e[l]ster'
    # 1) arquivos de hoje; 2) todo o histórico: conteúdo de cada commit e mensagens de commit.
    achados=$(git ls-files -z | xargs -0 grep -inE "$termos" | grep -viE '@c[e]sar\.school')
    historico=$(git log --all -p --format=%B | grep -inE "$termos" | grep -viE '@c[e]sar\.school')
    # Fotos fora de exemplos/, hoje ou em qualquer commit antigo.
    fotos=$(git log --all --name-only --format= | sort -u | grep -iE '\.(jpe?g|png)$' | grep -v -e '^exemplos/' -e '^evidencias/swagger\.png$')
    if [ -n "$achados" ] || [ -n "$historico" ] || [ -n "$fotos" ]; then
        echo "FALHOU: conteúdo proibido no repositório ou no histórico:"
        echo "$achados"
        echo "$historico"
        echo "$fotos"
        exit 1
    fi
    echo "OK: nenhum termo proibido nem foto fora de exemplos/ (exceto o print do Swagger), nos arquivos e no histórico"
