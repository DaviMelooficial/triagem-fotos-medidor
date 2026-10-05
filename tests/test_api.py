"""Testes de ponta a ponta: mandam a foto para o serviço rodando, igual a um curl.

Precisam do Ollama aberto e do modelo baixado (veja o README).
"""

import io
from pathlib import Path

import httpx
from PIL import Image

EXEMPLOS = Path(__file__).parent.parent / "exemplos"
TIMEOUT = 300  # o modelo pode levar dezenas de segundos por foto


def enviar(url, arquivo, conteudo, tipo="image/jpeg"):
    return httpx.post(f"{url}/extrair", files={"foto": (arquivo, conteudo, tipo)}, timeout=TIMEOUT)


def enviar_exemplo(url, nome):
    resposta = enviar(url, nome, (EXEMPLOS / nome).read_bytes())
    assert resposta.status_code == 200
    return resposta.json()


def test_display_registro_103(url):
    corpo = enviar_exemplo(url, "01_display_registro_103.jpg")
    assert corpo["numero_medidor"] == "3223400069"
    assert corpo["funcao"] == "103"
    assert corpo["leitura"] == "09888"  # zero à esquerda preservado
    # Contrato da resposta: confiança por campo e geral, entre 0 e 1.
    assert set(corpo["confianca"]) == {"numero_medidor", "funcao", "leitura", "geral"}
    assert 0 < corpo["confianca"]["geral"] <= 1
    assert isinstance(corpo["precisa_revisao"], bool)


def test_display_registro_03(url):
    corpo = enviar_exemplo(url, "02_display_registro_03.jpg")
    assert corpo["numero_medidor"] == "3223400069"
    assert corpo["funcao"] == "03"
    assert corpo["leitura"] == "17106"


def test_tampa_opaca_vai_para_revisao(url):
    corpo = enviar_exemplo(url, "03_tampa_opaca.jpg")
    assert corpo["precisa_revisao"] is True
    assert corpo["confianca"]["geral"] < 1


def test_tampa_suja_vai_para_revisao(url):
    corpo = enviar_exemplo(url, "04_tampa_suja.jpg")
    assert corpo["precisa_revisao"] is True
    assert corpo["confianca"]["geral"] < 1


def test_arquivo_que_nao_e_imagem_devolve_400(url):
    resposta = enviar(url, "leitura.txt", b"isto nao e uma foto", tipo="text/plain")
    assert resposta.status_code == 400
    assert "não é uma imagem válida" in resposta.text


def test_imagem_webp_devolve_400(url):
    # É uma imagem de verdade, mas num formato que o serviço não aceita.
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="WEBP")
    resposta = enviar(url, "foto.webp", buffer.getvalue(), tipo="image/webp")
    assert resposta.status_code == 400
    assert "Formato WEBP não aceito" in resposta.text
