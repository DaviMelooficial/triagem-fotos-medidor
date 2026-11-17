"""Testes das variáveis de entrada e do KS/PSI, sem serviço e sem modelo."""

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

import drift
import monitoramento

FOTO = (Path(__file__).parent.parent / "exemplos" / "01_display_registro_103.jpg").read_bytes()


def variaveis(imagem: Image.Image) -> dict:
    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG", quality=90)
    bruto = buffer.getvalue()
    with Image.open(io.BytesIO(bruto)) as aberta:
        aberta.load()
        return monitoramento.variaveis_entrada(aberta, bruto)


def test_foto_escura_e_borrada_mudam_as_variaveis_certas():
    original = Image.open(io.BytesIO(FOTO)).convert("RGB")
    normal = variaveis(original)
    escura = variaveis(ImageEnhance.Brightness(original).enhance(0.3))
    borrada = variaveis(original.filter(ImageFilter.GaussianBlur(5)))
    assert escura["media_l"] < normal["media_l"] / 2
    assert escura["frac_escuros"] > normal["frac_escuros"]
    assert borrada["nitidez_laplaciano"] < normal["nitidez_laplaciano"] / 5
    assert abs(borrada["media_l"] - normal["media_l"]) < 5  # borrar não muda o brilho


def _linhas(valores: np.ndarray) -> list[dict]:
    return [{"entrada.media_l": float(v)} for v in valores]


def test_ks_e_psi_nao_acusam_mesma_distribuicao_e_acusam_deslocada():
    rng = np.random.default_rng(0)
    referencia = {"n": 500, "variaveis": {"entrada.media_l": rng.normal(130, 15, 500).tolist()}, "hist_l_soma": [1] * 16}
    igual = drift.comparar(_linhas(rng.normal(130, 15, 500)), referencia)["variaveis"]["entrada.media_l"]
    escura = drift.comparar(_linhas(rng.normal(60, 15, 500)), referencia)["variaveis"]["entrada.media_l"]
    assert not igual["drift"] and not igual["psi_drift"] and igual["psi"] < 0.1
    assert escura["drift_bonferroni"] and escura["psi_drift"] and escura["psi"] > 0.25


def test_psi_com_amostra_pequena_nao_marca_drift_so_por_ruido():
    rng = np.random.default_rng(1)
    referencia = {"n": 60, "variaveis": {"entrada.media_l": rng.normal(130, 15, 60).tolist()}, "hist_l_soma": [1] * 16}
    pequeno = drift.comparar(_linhas(rng.normal(130, 15, 30)), referencia)["variaveis"]["entrada.media_l"]
    assert pequeno["psi_limite_ruido_95"] > 0.25  # com 60 x 30 amostras o ruído do PSI já passa de 0,25
    assert not pequeno["psi_drift"]


def test_variavel_binaria_usa_qui_quadrado_e_acusa_34_para_51_por_cento():
    # Caso real da carga drift-8: precisa_revisao 34 % (68/200) na referência e 51,4 % (89/173) no log.
    # O KS em variável 0/1 dava p = 0,006, que não passa no Bonferroni; o qui-quadrado passa.
    referencia = {"n": 200, "variaveis": {"saida.precisa_revisao": [True] * 68 + [False] * 132}, "hist_l_soma": [1] * 16}
    lote = [{"saida.precisa_revisao": v} for v in [True] * 89 + [False] * 84]
    v = drift.comparar(lote, referencia)["variaveis"]["saida.precisa_revisao"]
    assert v["teste"] == "qui2" and v["drift_bonferroni"]
    assert v["diferenca"]["diferenca_pp"] == 17.45 and v["diferenca"]["ic95_pp"][0] > 0


def test_categoria_de_texto_e_contagem_baixa_usam_fisher():
    referencia = {"n": 40, "variaveis": {"saida.funcao": ["03"] * 38 + ["kWh"] * 2}, "hist_l_soma": [1] * 16}
    lote = [{"saida.funcao": f} for f in ["03"] * 30 + ["kWh"] * 8 + [None] * 2]
    v = drift.comparar(lote, referencia)["variaveis"]["saida.funcao"]
    assert v["teste"] == "fisher_montecarlo" and v["proporcoes_lote"]["nulo"] == 0.05
