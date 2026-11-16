"""Testes do monitoramento com o serviço de verdade no modo simulado (não precisam do Ollama).

Conferem o que o enunciado pede: o rastro de cada pedido vai para um arquivo append-only, com hora
detalhada, variáveis de entrada e saída, erros 400 também logados, endpoint que devolve as variáveis
e o /metrics no formato Prometheus.
"""

import io
import json
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image

EXEMPLOS = Path(__file__).parent.parent / "exemplos"
FOTO = (EXEMPLOS / "01_display_registro_103.jpg").read_bytes()
# ISO 8601 com microssegundos e fuso: 2026-11-16T20:15:03.123456-03:00
HORA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}[+-]\d{2}:\d{2}$")


def enviar(base, conteudo=FOTO, nome="foto.jpg", tipo="image/jpeg"):
    return httpx.post(f"{base}/extrair", files={"foto": (nome, conteudo, tipo)}, timeout=60)


def conteudo_do_log(pasta: Path) -> str:
    """Todos os arquivos, do mais antigo (.N) ao atual, concatenados."""
    rotacionados = sorted(pasta.glob("inferencia.jsonl.*"), key=lambda p: int(p.suffix[1:]), reverse=True)
    return "".join(p.read_text(encoding="utf-8") for p in [*rotacionados, pasta / "inferencia.jsonl"] if p.exists())


def eventos(pasta: Path, request_id: str) -> list[dict]:
    linhas = [json.loads(linha) for linha in conteudo_do_log(pasta).splitlines()]
    return [e for e in linhas if e["request_id"] == request_id]


def test_pedido_ok_gera_eventos_pedido_e_resposta(servico_simulado):
    base, pasta = servico_simulado
    resposta = enviar(base, nome="nome_do_cliente_secreto.jpg")
    assert resposta.status_code == 200
    assert resposta.json()["backend"] == "simulado"
    pedido, final = eventos(pasta, resposta.headers["x-request-id"])
    assert (pedido["evento"], final["evento"]) == ("pedido", "resposta")
    assert HORA_ISO.match(final["ts"]) and final["ts_epoch"] >= pedido["ts_epoch"]
    assert final["backend"] == "simulado" and final["http_status"] == 200 and final["status"] == "ok"
    assert final["latencia_total_s"] >= final["saida"]["latencia_modelo_s"] > 0
    entrada = final["entrada"]
    assert (entrada["largura"], entrada["altura"], entrada["formato"]) == (900, 1600, "JPEG")
    assert entrada["bytes"] == len(FOTO) and len(entrada["sha256"]) == 64
    assert 0 <= entrada["media_l"] <= 255 and entrada["nitidez_laplaciano"] > 0
    assert len(entrada["hist_l"]) == 16 and abs(sum(entrada["hist_l"]) - 1) < 1e-3
    saida = final["saida"]
    assert set(saida) >= {"confianca_geral", "precisa_revisao", "campos_nulos", "latencias_chamadas_s", "leitura_suspeita"}
    assert len(saida["latencias_chamadas_s"]) == final["n_respostas"]


def test_log_nao_guarda_nome_do_arquivo_nem_numeros_lidos(servico_simulado):
    base, pasta = servico_simulado
    resposta = enviar(base, nome="nome_do_cliente_secreto.jpg")
    corpo = resposta.json()
    texto = conteudo_do_log(pasta)
    assert "nome_do_cliente_secreto" not in texto
    assert corpo["numero_medidor"] not in texto  # o número de série lido nunca vai para o log


def test_log_e_append_only(servico_simulado):
    base, pasta = servico_simulado
    antes = conteudo_do_log(pasta)
    for _ in range(4):  # o suficiente para rodar o arquivo (LOG_MAX_BYTES pequeno no teste)
        assert enviar(base).status_code == 200
    depois = conteudo_do_log(pasta)
    assert depois.startswith(antes) and len(depois) > len(antes)  # só acrescenta, nunca reescreve
    assert (pasta / "inferencia.jsonl.1").exists()  # houve rotação


def test_arquivo_que_nao_e_imagem_devolve_400_e_fica_no_log(servico_simulado):
    base, pasta = servico_simulado
    resposta = enviar(base, b"isto nao e uma foto", "leitura.txt", "text/plain")
    assert resposta.status_code == 400
    assert "não é uma imagem válida" in resposta.text
    (final,) = eventos(pasta, resposta.headers["x-request-id"])  # não chegou ao modelo: só o evento final
    assert final["status"] == "erro_cliente" and final["http_status"] == 400
    assert "não é uma imagem válida" in final["erro_msg"]
    assert final["entrada"] is None and final["saida"] is None


def test_webp_devolve_400_com_variaveis_de_entrada_no_log(servico_simulado):
    base, pasta = servico_simulado
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="WEBP")
    resposta = enviar(base, buffer.getvalue(), "foto.webp", "image/webp")
    assert resposta.status_code == 400 and "Formato WEBP não aceito" in resposta.text
    pedido, final = eventos(pasta, resposta.headers["x-request-id"])
    assert final["erro_tipo"] == "InvalidArgument" and final["entrada"]["formato"] == "WEBP"


def test_nome_de_arquivo_longo_e_content_type_image_jpg_funcionam(servico_simulado):
    # Com `foto: Path` o nome de 300 caracteres dava 500 (o BentoML gravava o arquivo com esse nome).
    base, _ = servico_simulado
    assert enviar(base, nome="a" * 300 + ".jpg").status_code == 200
    assert enviar(base, tipo="image/jpg").status_code == 200  # o conversor padrão do BentoML dava 500


def test_jpeg_cortado_devolve_400(servico_simulado):
    base, _ = servico_simulado
    assert enviar(base, FOTO[: len(FOTO) // 3]).status_code == 400


def test_monitor_variaveis_json_csv_e_filtros(servico_simulado):
    base, pasta = servico_simulado
    enviar(base)
    enviar(base, b"nao e foto", "x.txt", "text/plain")
    respostas = httpx.get(f"{base}/monitor/variaveis").json()
    no_log = [json.loads(linha) for linha in conteudo_do_log(pasta).splitlines()]
    assert respostas["n"] == sum(e["evento"] == "resposta" for e in no_log)  # lê também os arquivos rotacionados
    linha_ok = next(linha for linha in respostas["linhas"] if linha["status"] == "ok")
    assert {"entrada.media_l", "entrada.hist_l_15", "saida.confianca_geral", "latencia_total_s"} <= set(linha_ok)
    erros = httpx.get(f"{base}/monitor/variaveis", params={"status": "erro_cliente"}).json()
    assert erros["n"] >= 1 and all(linha["http_status"] == 400 for linha in erros["linhas"])
    pedidos = httpx.get(f"{base}/monitor/variaveis", params={"evento": "pedido", "limite": 2}).json()
    assert pedidos["n"] == 2 and all(linha["evento"] == "pedido" for linha in pedidos["linhas"])
    csv = httpx.get(f"{base}/monitor/variaveis", params={"formato": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("ts,ts_epoch,evento")
    futuro = httpx.get(f"{base}/monitor/variaveis", params={"inicio": "2999-01-01T00:00:00"}).json()
    assert futuro["n"] == 0
    assert httpx.get(f"{base}/monitor/variaveis", params={"inicio": "ontem"}).status_code == 400


def test_monitor_resumo_e_drift(servico_simulado):
    base, _ = servico_simulado
    enviar(base)
    resumo = httpx.get(f"{base}/monitor/resumo").json()
    caixa = resumo["variaveis"]["entrada.media_l"]
    assert caixa["min"] <= caixa["q1"] <= caixa["mediana"] <= caixa["q3"] <= caixa["max"]
    assert sum(caixa["histograma"]["contagens"]) == caixa["n"]
    drift = httpx.get(f"{base}/monitor/drift").json()
    assert drift["n_referencia"] == 200
    v = drift["variaveis"]["entrada.media_l"]
    assert 0 <= v["ks_estatistica"] <= 1 and 0 <= v["ks_p_valor"] <= 1 and v["psi"] >= 0


def test_metrics_formato_prometheus_com_metricas_proprias(servico_simulado):
    base, _ = servico_simulado
    enviar(base)
    texto = httpx.get(f"{base}/metrics").text
    assert "# TYPE bentoml_service_request_duration_seconds histogram" in texto
    assert re.search(r'bentoml_service_request_duration_seconds_bucket\{endpoint="/extrair".*le="600.0"', texto)
    assert re.search(r'bentoml_service_request_total\{endpoint="/extrair",http_response_code="200"', texto)
    assert re.search(r'triagem_predicoes_total\{backend="simulado",precisa_revisao="(true|false)",status="ok"\}', texto)
    for nome in ("triagem_confianca_geral_bucket", "triagem_latencia_chamada_modelo_segundos_bucket", "triagem_entrada_luminancia_media_bucket"):
        assert nome in texto


@pytest.mark.parametrize("leitura,suspeita", [("000000", True), ("0123456789", True), ("09888", False), (None, False), ("12", False)])
def test_sequencia_suspeita(leitura, suspeita):
    import monitoramento

    assert monitoramento._sequencia_suspeita(leitura) is suspeita
