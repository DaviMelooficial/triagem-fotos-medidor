-- Consultas de exemplo no monitoring/metrics.duckdb. Rode: just duckdb-consultas
-- (ou: uv run python monitoring/metrics_to_duckdb.py --consultas). Cada consulta começa pelo comentário que a explica.

-- 1) Pedidos ao /extrair por código HTTP entre a primeira e a última coleta (contador: última - primeira).
--    Série que ainda não existia na primeira coleta (ex.: o primeiro 400 veio depois) conta como 0.
WITH limites AS (SELECT min(coletado_em) AS t0, max(coletado_em) AS t1 FROM metricas),
janela AS (
    SELECT http_code,
           sum(valor) FILTER (WHERE coletado_em = t1) - coalesce(sum(valor) FILTER (WHERE coletado_em = t0), 0) AS pedidos
    FROM metricas, limites
    WHERE serie = 'bentoml_service_request_total' AND endpoint = '/extrair'
    GROUP BY http_code
)
SELECT http_code, pedidos, round(pedidos / sum(pedidos) OVER (), 4) AS fracao
FROM janela ORDER BY http_code;

-- 2) Pedidos por intervalo de coleta (5 s): diferença entre coletas seguidas, como o increase() do Prometheus.
SELECT coletado_em, sum(valor) - lag(sum(valor)) OVER (ORDER BY coletado_em) AS pedidos_no_intervalo
FROM metricas
WHERE serie = 'bentoml_service_request_total' AND endpoint = '/extrair'
GROUP BY coletado_em ORDER BY coletado_em LIMIT 8;

-- 3) Direto do log (tabela eventos): latência e revisão por backend, só respostas 200.
SELECT backend, count(*) AS n,
       round(quantile_cont(latencia_total_s, 0.5), 3) AS lat_mediana_s,
       round(quantile_cont(latencia_total_s, 0.95), 3) AS lat_p95_s,
       round(avg("saida.precisa_revisao"::INT), 3) AS fracao_revisao
FROM eventos WHERE evento = 'resposta' AND status = 'ok' GROUP BY backend;
