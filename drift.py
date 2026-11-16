"""Testes de drift de cada variável do log contra o conjunto de referência (monitoring/referencia.json).

Cada variável recebe o teste certo para o seu tipo:
- contínua (as 13 variáveis da foto): KS (Kolmogorov-Smirnov, duas amostras), a maior distância entre
  as duas distribuições acumuladas;
- discreta ou categórica (as da saída: confiança 0,33/0,67/1,0, precisa_revisao, função lida...): qui-quadrado
  de homogeneidade na tabela 2 x k (referência x lote); Fisher exato quando alguma contagem esperada é < 5.
  KS em variável com poucos valores (cheia de empates) é conservador demais e esconde mudança real.
Em todas: p-valor < alfa = é improvável que as duas amostras venham da mesma distribuição. Como são
vários testes de uma vez, devolvemos também a lista com correção de Bonferroni (alfa / número de testes,
contando o KS por pixel).

- PSI (Population Stability Index): soma de (p_lote - p_ref) * ln(p_lote / p_ref) nas faixas definidas
  pelos decis da referência (como na aula 008), ou pelas próprias categorias, com +0,5 em cada contagem
  para faixa vazia não virar ln(0). Leitura usual: < 0,10 estável; 0,10-0,25 moderado; > 0,25 forte.
  Cuidado com amostra pequena: SEM drift nenhum o PSI já vale em média (faixas-1) x (1/n_ref + 1/n_lote)
  (aproximação qui-quadrado). Por isso devolvemos "psi_limite_ruido_95" = (1/n_ref + 1/n_lote) x
  qui2_95%(faixas-1) e só marcamos "psi_drift" quando o PSI passa de 0,25 E desse limite. O PSI não tem
  correção de multiplicidade (ver monitoring/README.md).
- KS por pixel (como na aula 003): compara o histograma de luminância somado de todas as fotos,
  com o número de fotos (não de pixels) como tamanho de amostra no p-valor.
- precisa_revisao é o KPI da triagem: também sai a diferença em pontos percentuais com IC de 95 %.
"""

import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

ARQUIVO_REFERENCIA = Path(__file__).parent / "monitoring" / "referencia.json"
SUAVIZACAO = 0.5  # somado a cada contagem do PSI, para faixa vazia não virar ln(0)
FAIXAS_PSI = 10

# Nome achatado do log -> tipo. Contínuas: KS + PSI por decis. Categóricas: qui-quadrado/Fisher + PSI por categoria.
CONTINUAS = [
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
]
CATEGORICAS = [
    "saida.confianca_geral",
    "saida.confianca_numero_medidor",
    "saida.confianca_funcao",
    "saida.confianca_leitura",
    "saida.precisa_revisao",
    "saida.n_campos_nulos",
    "saida.leitura_suspeita",
    "saida.respostas_identicas",
    "saida.votos_distintos_leitura",
    "saida.leitura_n_digitos",
    "saida.funcao",
]
VARIAVEIS = CONTINUAS + CATEGORICAS
N_TESTES = len(VARIAVEIS) + 1  # + KS por pixel


def carregar_referencia(caminho: Path = ARQUIVO_REFERENCIA) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _numeros(valores) -> np.ndarray:
    limpos = [float(v) for v in valores if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return np.asarray(limpos, dtype=float)


def _categoria(valor) -> str:
    """Texto estável para contar categorias: True/1/1.0 viram "1", None vira "nulo"."""
    if valor is None:
        return "nulo"
    if isinstance(valor, bool):
        return str(int(valor))
    if isinstance(valor, (int, float)):
        return f"{float(valor):g}"
    return str(valor)


def _psi(conta_ref: np.ndarray, conta_lote: np.ndarray) -> float:
    p_ref = (conta_ref + SUAVIZACAO) / (conta_ref + SUAVIZACAO).sum()
    p_lote = (conta_lote + SUAVIZACAO) / (conta_lote + SUAVIZACAO).sum()
    return float(((p_lote - p_ref) * np.log(p_lote / p_ref)).sum())


def _campos_psi(conta_ref, conta_lote, n_ref, n_lote) -> dict:
    valor = _psi(conta_ref, conta_lote)
    faixas = len(conta_ref)
    ruido = (1 / n_ref + 1 / n_lote) * stats.chi2.ppf(0.95, max(faixas - 1, 1))
    return {
        "psi": round(valor, 4),
        "psi_leitura": "estável" if valor < 0.10 else "moderado" if valor < 0.25 else "forte",
        "psi_faixas": faixas,
        "psi_limite_ruido_95": round(float(ruido), 4),
        "psi_drift": bool(valor > max(0.25, ruido)),
    }


def comparar_continua(ref: np.ndarray, lote: np.ndarray) -> dict:
    ks = stats.ks_2samp(ref, lote)
    internos = np.unique(np.quantile(ref, np.linspace(0, 1, FAIXAS_PSI + 1)[1:-1]))
    limites = [-math.inf, *internos.tolist(), math.inf]
    conta_ref = np.histogram(ref, bins=limites)[0].astype(float)
    conta_lote = np.histogram(lote, bins=limites)[0].astype(float)
    return {
        "tipo": "continua",
        "teste": "ks",
        "n_ref": int(len(ref)),
        "n_lote": int(len(lote)),
        "media_ref": round(float(ref.mean()), 4),
        "media_lote": round(float(lote.mean()), 4),
        "estatistica": round(float(ks.statistic), 4),
        "p_valor": float(f"{ks.pvalue:.4g}"),
        **_campos_psi(conta_ref, conta_lote, len(ref), len(lote)),
    }


def comparar_categorica(ref: list[str], lote: list[str]) -> dict:
    categorias = sorted(set(ref) | set(lote))
    conta_ref = np.array([ref.count(c) for c in categorias], dtype=float)
    conta_lote = np.array([lote.count(c) for c in categorias], dtype=float)
    tabela = np.vstack([conta_ref, conta_lote]).astype(int)
    if len(categorias) < 2:
        teste, estatistica, p = "nenhum (uma categoria só)", 0.0, 1.0
    else:
        esperado = stats.contingency.expected_freq(tabela)
        if esperado.min() >= 5:
            r = stats.chi2_contingency(tabela)  # 2 x 2 usa a correção de Yates (padrão do scipy)
            teste, estatistica, p = "qui2", float(r.statistic), float(r.pvalue)
        elif len(categorias) == 2:
            r = stats.fisher_exact(tabela)
            teste, estatistica, p = "fisher", float(r.statistic), float(r.pvalue)
        else:  # 2 x k com contagem baixa: Fisher por Monte Carlo, com semente fixa (resultado reproduzível)
            r = stats.fisher_exact(tabela, method=stats.MonteCarloMethod(n_resamples=20000, rng=0))
            teste, estatistica, p = "fisher_montecarlo", float(r.statistic), float(r.pvalue)
    return {
        "tipo": "categorica",
        "teste": teste,
        "n_ref": len(ref),
        "n_lote": len(lote),
        "proporcoes_ref": {c: round(n / len(ref), 4) for c, n in zip(categorias, conta_ref)},
        "proporcoes_lote": {c: round(n / len(lote), 4) for c, n in zip(categorias, conta_lote)},
        "estatistica": round(estatistica, 4) if math.isfinite(estatistica) else None,
        "p_valor": float(f"{p:.4g}"),
        **_campos_psi(conta_ref, conta_lote, len(ref), len(lote)),
    }


def diferenca_proporcao(ref: list[str], lote: list[str]) -> dict:
    """Diferença lote - referência na fração de "1", em pontos percentuais, com IC 95 % (Wald)."""
    p1, p2 = ref.count("1") / len(ref), lote.count("1") / len(lote)
    erro = math.sqrt(p1 * (1 - p1) / len(ref) + p2 * (1 - p2) / len(lote))
    diff = p2 - p1
    return {
        "fracao_ref": round(p1, 4),
        "fracao_lote": round(p2, 4),
        "diferenca_pp": round(100 * diff, 2),
        "ic95_pp": [round(100 * (diff - 1.96 * erro), 2), round(100 * (diff + 1.96 * erro), 2)],
    }


def comparar(linhas: list[dict], referencia: dict, alfa: float = 0.05) -> dict:
    """Recebe linhas achatadas do log (evento "resposta", status ok) e devolve o teste e o PSI por variável."""
    alfa_bonf = alfa / N_TESTES
    resultado = {
        "backend_referencia": referencia.get("backend"),
        "n_lote": len(linhas),
        "n_referencia": referencia["n"],
        "alfa": alfa,
        "n_testes": N_TESTES,
        "alfa_bonferroni": alfa_bonf,
        "variaveis": {},
    }
    for nome in VARIAVEIS:
        if nome in CONTINUAS:
            ref = _numeros(referencia["variaveis"].get(nome, []))
            lote = _numeros(linha.get(nome) for linha in linhas)
        else:
            ref = [_categoria(v) for v in referencia["variaveis"].get(nome, [])]
            lote = [_categoria(linha.get(nome)) for linha in linhas if nome in linha]
        if len(ref) < 2 or len(lote) < 2:
            resultado["variaveis"][nome] = {"n_lote": int(len(lote)), "erro": "amostra insuficiente (< 2)"}
            continue
        item = comparar_continua(ref, lote) if nome in CONTINUAS else comparar_categorica(ref, lote)
        item["drift"] = item["p_valor"] < alfa
        item["drift_bonferroni"] = item["p_valor"] < alfa_bonf
        if nome == "saida.precisa_revisao":
            item["diferenca"] = diferenca_proporcao(ref, lote)
        resultado["variaveis"][nome] = item
    n, m = referencia["n"], len(linhas)
    if m:
        # Valor crítico do KS (menor D que dá p < alfa) para estes tamanhos de amostra, com e sem Bonferroni.
        c = lambda a: math.sqrt(-math.log(a / 2) / 2)  # noqa: E731  (aproximação assintótica do valor crítico)
        escala = math.sqrt((n + m) / (n * m))
        resultado["ks_d_critico"] = round(c(alfa) * escala, 4)
        resultado["ks_d_critico_bonferroni"] = round(c(alfa_bonf) * escala, 4)
    # KS por pixel (aula 003): histograma de luminância somado, n efetivo = fotos.
    hist_lote = [linha.get(f"entrada.hist_l_{i:02d}") for linha in linhas for i in range(16)]
    if linhas and None not in hist_lote:
        soma_lote = np.asarray(hist_lote, dtype=float).reshape(len(linhas), 16).sum(axis=0)
        soma_ref = np.asarray(referencia["hist_l_soma"], dtype=float)
        cdf_lote, cdf_ref = np.cumsum(soma_lote) / soma_lote.sum(), np.cumsum(soma_ref) / soma_ref.sum()
        d = float(np.abs(cdf_lote - cdf_ref).max())
        n_eff = n * m / (n + m)
        p = float(stats.kstwobign.sf(d * (math.sqrt(n_eff) + 0.12 + 0.11 / math.sqrt(n_eff))))
        resultado["ks_pixels_luminancia"] = {
            "estatistica": round(d, 4), "p_valor": float(f"{p:.4g}"), "drift": p < alfa, "drift_bonferroni": p < alfa_bonf,
        }
    itens = resultado["variaveis"].items()
    resultado["variaveis_com_drift"] = [k for k, v in itens if v.get("drift")]
    resultado["variaveis_com_drift_bonferroni"] = [k for k, v in itens if v.get("drift_bonferroni")]
    resultado["variaveis_com_drift_psi"] = [k for k, v in itens if v.get("psi_drift")]
    return resultado
