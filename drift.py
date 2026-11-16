"""KS e PSI de cada variável do log contra o conjunto de referência (monitoring/referencia.json).

- KS (Kolmogorov-Smirnov, duas amostras): maior distância entre as duas distribuições acumuladas.
  p-valor < 0,05 = é improvável que as duas amostras venham da mesma distribuição.
- PSI (Population Stability Index): soma de (p_lote - p_ref) * ln(p_lote / p_ref) nas faixas definidas
  pelos decis da referência (como na aula 008), com +0,5 em cada contagem para faixa vazia não virar
  ln(0). Leitura usual: < 0,10 estável; 0,10-0,25 moderado; > 0,25 forte. Variável com até 10 valores
  distintos (ex.: confiança 0,33/0,67/1,0) usa os próprios valores como faixas.
  Cuidado com amostra pequena: SEM drift nenhum o PSI já vale em média (faixas-1) x (1/n_ref + 1/n_lote)
  (aproximação qui-quadrado). Por isso devolvemos também "psi_limite_ruido_95" = (1/n_ref + 1/n_lote) x
  qui2_95%(faixas-1) e só marcamos "psi_drift" quando o PSI passa de 0,25 E desse limite.
- KS por pixel (como na aula 003): compara o histograma de luminância somado de todas as fotos,
  com o número de fotos (não de pixels) como tamanho de amostra no p-valor.
"""

import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

ARQUIVO_REFERENCIA = Path(__file__).parent / "monitoring" / "referencia.json"
SUAVIZACAO = 0.5  # somado a cada contagem do PSI, para faixa vazia não virar ln(0)
FAIXAS_PSI = 10
LIMITE_CATEGORICO = 10

# Variáveis comparadas e de onde vêm (nome achatado do log).
VARIAVEIS = [
    "entrada.megapixels",
    "entrada.razao_aspecto",
    "entrada.bytes_por_pixel",
    "entrada.media_l",
    "entrada.media_r",
    "entrada.media_g",
    "entrada.media_b",
    "entrada.contraste_l",
    "entrada.croma_medio",
    "entrada.nitidez_laplaciano",
    "entrada.frac_escuros",
    "entrada.frac_estourados",
    "entrada.entropia_l",
    "saida.confianca_geral",
    "saida.precisa_revisao",
    "saida.n_campos_nulos",
    "saida.leitura_suspeita",
]


def carregar_referencia(caminho: Path = ARQUIVO_REFERENCIA) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _numeros(valores) -> np.ndarray:
    limpos = [float(v) for v in valores if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return np.asarray(limpos, dtype=float)


def psi(referencia: np.ndarray, lote: np.ndarray) -> tuple[float, int]:
    """PSI com faixas pelos decis da referência (extremos abertos) ou por categoria."""
    if len(np.unique(np.concatenate([referencia, lote]))) <= LIMITE_CATEGORICO:
        categorias = np.unique(np.concatenate([referencia, lote]))
        conta_ref = np.array([(referencia == c).sum() for c in categorias], dtype=float)
        conta_lote = np.array([(lote == c).sum() for c in categorias], dtype=float)
    else:
        internos = np.unique(np.quantile(referencia, np.linspace(0, 1, FAIXAS_PSI + 1)[1:-1]))
        limites = [-math.inf, *internos.tolist(), math.inf]
        conta_ref = np.histogram(referencia, bins=limites)[0].astype(float)
        conta_lote = np.histogram(lote, bins=limites)[0].astype(float)
    p_ref = (conta_ref + SUAVIZACAO) / (conta_ref + SUAVIZACAO).sum()
    p_lote = (conta_lote + SUAVIZACAO) / (conta_lote + SUAVIZACAO).sum()
    return float(((p_lote - p_ref) * np.log(p_lote / p_ref)).sum()), len(p_ref)


def _leitura_psi(valor: float) -> str:
    return "estável" if valor < 0.10 else "moderado" if valor < 0.25 else "forte"


def comparar(linhas: list[dict], referencia: dict, alfa: float = 0.05) -> dict:
    """Recebe linhas achatadas do log (evento "resposta", status ok) e devolve KS/PSI por variável."""
    resultado = {"n_lote": len(linhas), "n_referencia": referencia["n"], "alfa": alfa, "variaveis": {}}
    for nome in VARIAVEIS:
        ref = _numeros(referencia["variaveis"].get(nome, []))
        lote = _numeros(linha.get(nome) for linha in linhas)
        if len(ref) < 2 or len(lote) < 2:
            resultado["variaveis"][nome] = {"n_lote": int(len(lote)), "erro": "amostra insuficiente (< 2)"}
            continue
        ks = stats.ks_2samp(ref, lote)
        valor_psi, faixas = psi(ref, lote)
        ruido = (1 / len(ref) + 1 / len(lote)) * stats.chi2.ppf(0.95, max(faixas - 1, 1))
        resultado["variaveis"][nome] = {
            "n_ref": int(len(ref)),
            "n_lote": int(len(lote)),
            "media_ref": round(float(ref.mean()), 4),
            "media_lote": round(float(lote.mean()), 4),
            "ks_estatistica": round(float(ks.statistic), 4),
            "ks_p_valor": float(f"{ks.pvalue:.4g}"),
            "ks_drift": bool(ks.pvalue < alfa),
            "psi": round(valor_psi, 4),
            "psi_leitura": _leitura_psi(valor_psi),
            "psi_faixas": faixas,
            "psi_limite_ruido_95": round(float(ruido), 4),
            "psi_drift": bool(valor_psi > max(0.25, ruido)),
        }
    # KS por pixel (aula 003): histograma de luminância somado, n efetivo = fotos.
    hist_lote = [linha.get(f"entrada.hist_l_{i:02d}") for linha in linhas for i in range(16)]
    if linhas and None not in hist_lote:
        soma_lote = np.asarray(hist_lote, dtype=float).reshape(len(linhas), 16).sum(axis=0)
        soma_ref = np.asarray(referencia["hist_l_soma"], dtype=float)
        cdf_lote, cdf_ref = np.cumsum(soma_lote) / soma_lote.sum(), np.cumsum(soma_ref) / soma_ref.sum()
        d = float(np.abs(cdf_lote - cdf_ref).max())
        n_eff = referencia["n"] * len(linhas) / (referencia["n"] + len(linhas))
        p = float(stats.kstwobign.sf(d * (math.sqrt(n_eff) + 0.12 + 0.11 / math.sqrt(n_eff))))
        resultado["ks_pixels_luminancia"] = {"estatistica": round(d, 4), "p_valor": float(f"{p:.4g}"), "drift": p < alfa}
    resultado["variaveis_com_drift_ks"] = [n for n, v in resultado["variaveis"].items() if v.get("ks_drift")]
    # Com 17 testes a 5 %, espera-se ~1 "drift" por acaso mesmo sem mudança nenhuma; Bonferroni divide o alfa.
    resultado["alfa_bonferroni"] = alfa / len(VARIAVEIS)
    resultado["variaveis_com_drift_ks_bonferroni"] = [
        n for n, v in resultado["variaveis"].items() if "ks_p_valor" in v and v["ks_p_valor"] < resultado["alfa_bonferroni"]
    ]
    resultado["variaveis_com_drift_psi"] = [n for n, v in resultado["variaveis"].items() if v.get("psi_drift")]
    return resultado
