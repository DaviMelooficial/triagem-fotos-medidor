"""Testes de ponta a ponta: mandam a foto para o serviço rodando, igual a um curl.

Precisam do Ollama aberto e do modelo baixado (veja o README).
"""

from pathlib import Path

import httpx

EXEMPLOS = Path(__file__).parent.parent / "exemplos"
TIMEOUT = 300  # o modelo pode levar dezenas de segundos por foto


def enviar(url, arquivo, conteudo, tipo="image/jpeg"):
    return httpx.post(f"{url}/extrair", files={"foto": (arquivo, conteudo, tipo)}, timeout=TIMEOUT)


def enviar_exemplo(url, nome):
    resposta = enviar(url, nome, (EXEMPLOS / nome).read_bytes())
    assert resposta.status_code == 200
    return resposta.json()


def test_medidor_eletronico_le_numero_funcao_e_leitura(url):
    corpo = enviar_exemplo(url, "01_eletronico.jpg")
    assert corpo["numero_medidor"] == "4071835526"
    assert corpo["funcao"] == "03"
    assert corpo["leitura"] == "052817"
    # Contrato da resposta: confiança por campo e geral, entre 0 e 1.
    assert set(corpo["confianca"]) == {"numero_medidor", "funcao", "leitura", "geral"}
    assert 0 < corpo["confianca"]["geral"] <= 1
    assert isinstance(corpo["precisa_revisao"], bool)


def test_medidor_de_rolete_le_o_registro_kwh(url):
    corpo = enviar_exemplo(url, "02_rolete.jpg")
    assert corpo["numero_medidor"] == "07291645"  # zero à esquerda preservado
    assert corpo["funcao"] == "kWh"
    assert corpo["leitura"] == "28504"


def test_foto_sem_medidor_vai_para_revisao(url):
    corpo = enviar_exemplo(url, "04_sem_medidor.jpg")
    assert corpo["leitura"] is None
    assert corpo["precisa_revisao"] is True


def test_arquivo_que_nao_e_imagem_devolve_400(url):
    resposta = enviar(url, "leitura.txt", b"isto nao e uma foto", tipo="text/plain")
    assert resposta.status_code == 400
    assert "não é uma imagem válida" in resposta.text
