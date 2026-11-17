"""Testes do monitoramento com o serviço de verdade no modo simulado (não precisam do Ollama).

Conferem o que o enunciado pede: o rastro de cada pedido vai para um arquivo append-only, com hora
detalhada, variáveis de entrada e saída, erros 400 também logados, endpoint que devolve as variáveis
e o /metrics no formato Prometheus.
"""

import io
import json
import logging
import re
import socket
import time
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
    # O evento "resposta" é gravado logo DEPOIS de a resposta HTTP sair: espera até 2 s por ele.
    for _ in range(40):
        linhas = [json.loads(linha) for linha in conteudo_do_log(pasta).splitlines()]
        achados = [e for e in linhas if e["request_id"] == request_id]
        if any(e["evento"] == "resposta" for e in achados):
            return achados
        time.sleep(0.05)
    return achados


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


def test_corpo_json_devolve_400(servico_simulado):
    # Antes: o texto "x" chegava ao endpoint e dava 500 (AttributeError).
    base, pasta = servico_simulado
    resposta = httpx.post(f"{base}/extrair", json={"foto": "x"}, timeout=60)
    assert resposta.status_code == 400 and "não é uma imagem válida" in resposta.text
    (final,) = eventos(pasta, resposta.headers["x-request-id"])
    assert final["status"] == "erro_cliente"


@pytest.mark.parametrize("campos", [{"arquivo": ("f.jpg", FOTO, "image/jpeg")}, {"foto": (None, "http://example.com/a.jpg")}])
def test_multipart_sem_arquivo_no_campo_foto_devolve_400(servico_simulado, campos):
    # Campo com outro nome dava 500 (UnicodeDecodeError no BentoML); "foto" como texto virava download de URL.
    base, pasta = servico_simulado
    resposta = httpx.post(f"{base}/extrair", files=campos, timeout=60)
    assert resposta.status_code == 400 and 'campo multipart \\"foto\\"' in resposta.text
    (final,) = eventos(pasta, resposta.headers["x-request-id"])
    assert final["erro_tipo"] == "campo_foto_ausente" and final["corpo_bytes"] > 0


def test_corpo_bytes_conta_upload_sem_content_length(servico_simulado):
    base, pasta = servico_simulado

    def pedacos():  # gerador = httpx manda em "chunked", sem Content-Length
        yield b'--limite123\r\nContent-Disposition: form-data; name="foto"; filename="f.jpg"\r\n'
        yield b"Content-Type: image/jpeg\r\n\r\n" + FOTO + b"\r\n--limite123--\r\n"

    resposta = httpx.post(f"{base}/extrair", content=pedacos(), timeout=60,
                          headers={"content-type": "multipart/form-data; boundary=limite123"})
    assert resposta.status_code == 200
    (_, final) = eventos(pasta, resposta.headers["x-request-id"])
    assert final["corpo_bytes"] > len(FOTO)


def test_upload_acima_do_limite_da_413_e_fica_no_log(servico_simulado):
    base, pasta = servico_simulado
    grande = b"\xff" * (2 * 1024 * 1024)  # 2 MB, acima do MAX_UPLOAD_MB=1 do teste
    resposta = enviar(base, grande)  # com Content-Length: recusado antes de ler
    assert resposta.status_code == 413
    (final,) = eventos(pasta, resposta.headers["x-request-id"])
    assert (final["status"], final["erro_tipo"]) == ("erro_cliente", "upload_grande") and final["corpo_bytes"] > 2 * 1024 * 1024

    def pedacos():  # sem Content-Length: recusado ao passar do limite durante a leitura
        yield b'--lim\r\nContent-Disposition: form-data; name="foto"; filename="f.jpg"\r\n\r\n'
        for _ in range(32):
            yield b"\xff" * 65536

    resposta = httpx.post(f"{base}/extrair", content=pedacos(), timeout=60, headers={"content-type": "multipart/form-data; boundary=lim"})
    assert resposta.status_code == 413
    (final,) = eventos(pasta, resposta.headers["x-request-id"])
    assert final["erro_tipo"] == "upload_grande"


@pytest.mark.parametrize("dados,tipo", [(b"foto=abc", "application/x-www-form-urlencoded"), (FOTO, "image/jpeg"), (b"oi", "text/plain")])
def test_content_type_que_nao_e_multipart_nem_json_da_400(servico_simulado, dados, tipo):
    # urlencoded "foto=abc" dava 500 (o BentoML tentava baixar "abc" como URL).
    base, pasta = servico_simulado
    resposta = httpx.post(f"{base}/extrair", content=dados, headers={"content-type": tipo}, timeout=60)
    assert resposta.status_code == 400
    (final,) = eventos(pasta, resposta.headers["x-request-id"])
    assert final["erro_tipo"] == "content_type_nao_aceito"


def test_cliente_que_desconecta_no_meio_do_upload_fica_marcado(servico_simulado):
    base, pasta = servico_simulado
    porta = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", porta)) as conexao:
        conexao.sendall(
            b"POST /extrair HTTP/1.1\r\nHost: localhost\r\nX-Carga-Id: desconecta-1\r\n"
            b"Content-Type: multipart/form-data; boundary=lim\r\nContent-Length: 100000\r\n\r\n" + b"x" * 1000
        )
    for _ in range(40):
        linhas = [json.loads(linha) for linha in conteudo_do_log(pasta).splitlines()]
        achados = [e for e in linhas if e.get("carga_id") == "desconecta-1"]
        if achados:
            break
        time.sleep(0.05)
    (final,) = achados
    assert (final["erro_tipo"], final["http_status"], final["corpo_bytes"]) == ("cliente_desconectou", 499, 1000)
    assert final["entrada"] is None  # corpo parcial não foi para o modelo


def test_filtro_do_terminal_nao_quebra_com_porcento_nem_mostra_os_bytes():
    from pydantic import TypeAdapter, ValidationError

    import service

    try:
        TypeAdapter(int).validate_python(b"\xff\xd8 100% foto")
    except ValidationError:
        registro = logging.LogRecord("x", logging.ERROR, __file__, 1, "Exception on %s [%s]", ("/extrair", "POST"), None)
        import sys

        registro.exc_info = sys.exc_info()
    service.SemTracebackDeErroDoCliente().filter(registro)
    texto = logging.Formatter("%(levelname)s %(message)s").format(registro)
    assert texto.startswith("INFO Exception on /extrair [POST] (erro do cliente, 400):")
    assert "100%" not in texto and "xd8" not in texto and "Traceback" not in texto


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
    assert erros["n"] >= 1 and all(400 <= linha["http_status"] < 500 for linha in erros["linhas"])
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
    assert drift["n_referencia"] == 200 and drift["backend_lote"] == "simulado"  # só o backend da referência
    v = drift["variaveis"]["entrada.media_l"]
    assert v["teste"] == "ks" and 0 <= v["estatistica"] <= 1 and 0 <= v["p_valor"] <= 1 and v["psi"] >= 0
    revisao = drift["variaveis"]["saida.precisa_revisao"]
    assert revisao["tipo"] == "categorica" and revisao["teste"] in ("qui2", "fisher") and "ic95_pp" in revisao["diferenca"]
    assert drift["variaveis"]["saida.funcao"]["tipo"] == "categorica"
    assert httpx.get(f"{base}/monitor/drift", params={"backend": "ollama"}).json()["n_lote"] == 0
    assert httpx.get(f"{base}/monitor/drift", params={"alfa": "2"}).status_code == 400


@pytest.mark.parametrize("parametros", [{"faixas": "0"}, {"faixas": "-3"}, {"faixas": "abc"}, {"limite": "-1"}, {"evento": "outro"}])
def test_monitor_parametro_invalido_da_400(servico_simulado, parametros):
    base, _ = servico_simulado
    enviar(base)
    assert httpx.get(f"{base}/monitor/resumo", params=parametros).status_code == 400


def test_carga_id_vai_para_o_log_e_filtra(servico_simulado):
    base, _ = servico_simulado
    resposta = httpx.post(f"{base}/extrair", files={"foto": ("f.jpg", FOTO, "image/jpeg")}, headers={"X-Carga-Id": "teste-carga-1"}, timeout=60)
    assert resposta.status_code == 200
    linhas = httpx.get(f"{base}/monitor/variaveis", params={"carga_id": "teste-carga-1"}).json()["linhas"]
    assert [linha["request_id"] for linha in linhas] == [resposta.headers["x-request-id"]]


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
