"""Análise do rastro: KS e PSI de cada variável contra a referência, histogramas e boxplots em SVG.

Lê o arquivo de log direto (inclusive os rotacionados), sem precisar do serviço rodando.
Uso:
  uv run python monitoring/analise.py --rotulo carga [--inicio 2026-11-16T20:00] [--fim ...]
  (ou: just analise carga)
Saída em monitoring/analise/: <rotulo>_ks_psi.txt, <rotulo>_histogramas.svg, <rotulo>_boxplots.svg, <rotulo>_latencia.svg
"""

import argparse
import math
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import drift  # noqa: E402
import monitor_api  # noqa: E402
import monitoramento  # noqa: E402

PASTA = Path(__file__).resolve().parent / "analise"
COR_REF, COR_LOTE, TINTA, TINTA_FRACA = "#2a78d6", "#eb6834", "#1f1f1e", "#6b6a63"
GRAFICOS = [
    ("entrada.media_l", "luminância média (0-255)"),
    ("entrada.contraste_l", "contraste: desvio-padrão de L"),
    ("entrada.nitidez_laplaciano", "nitidez: variância do Laplaciano"),
    ("entrada.megapixels", "megapixels"),
    ("entrada.frac_escuros", "fração de pixels escuros (L < 32)"),
    ("saida.confianca_geral", "confiança geral (saída)"),
]


def _estilo(eixo, titulo: str) -> None:
    eixo.set_title(titulo, fontsize=9, color=TINTA, loc="left")
    eixo.tick_params(labelsize=8, colors=TINTA_FRACA)
    for lado in ("top", "right"):
        eixo.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        eixo.spines[lado].set_color("#c9c8c1")
    eixo.grid(axis="y", color="#ecebe6", linewidth=0.8)
    eixo.set_axisbelow(True)


def _valores(linhas, nome) -> np.ndarray:
    return np.asarray([float(x[nome]) for x in linhas if x.get(nome) is not None], dtype=float)


def histogramas(linhas, ref, rotulo) -> Path:
    figura, eixos = plt.subplots(2, 3, figsize=(11, 6), facecolor="white")
    for eixo, (nome, titulo) in zip(eixos.flat, GRAFICOS):
        r, lote = np.asarray(ref["variaveis"][nome], dtype=float), _valores(linhas, nome)
        limites = np.histogram_bin_edges(np.concatenate([r, lote]) if len(lote) else r, bins=15)
        # densidade: as duas amostras têm tamanhos diferentes, então comparamos proporções
        eixo.hist(r, bins=limites, density=True, color=COR_REF, alpha=0.55, label=f"referência (n={len(r)})", edgecolor="white", linewidth=1)
        if len(lote):
            eixo.hist(lote, bins=limites, density=True, histtype="step", color=COR_LOTE, linewidth=2, label=f"log {rotulo} (n={len(lote)})")
        _estilo(eixo, titulo)
    eixos.flat[0].legend(fontsize=8, frameon=False)
    figura.suptitle(f"Histogramas (densidade): referência x log '{rotulo}'", fontsize=11, color=TINTA, x=0.01, ha="left")
    figura.tight_layout()
    caminho = PASTA / f"{rotulo}_histogramas.svg"
    figura.savefig(caminho)
    plt.close(figura)
    return caminho


def boxplots(linhas, ref, rotulo) -> Path:
    figura, eixos = plt.subplots(2, 3, figsize=(11, 6), facecolor="white")
    for eixo, (nome, titulo) in zip(eixos.flat, GRAFICOS):
        dados = [np.asarray(ref["variaveis"][nome], dtype=float), _valores(linhas, nome)]
        caixas = eixo.boxplot(dados, tick_labels=["referência", f"log {rotulo}"], patch_artist=True, widths=0.5,
                              medianprops={"color": TINTA, "linewidth": 2}, flierprops={"markersize": 3, "markeredgecolor": TINTA_FRACA})
        for caixa, cor in zip(caixas["boxes"], (COR_REF, COR_LOTE)):
            caixa.set_facecolor(cor)
            caixa.set_alpha(0.6)
        _estilo(eixo, titulo)
    figura.suptitle(f"Boxplots: referência x log '{rotulo}' (caixa = Q1-Q3, traço = mediana, haste = 1,5 IQR)", fontsize=11, color=TINTA, x=0.01, ha="left")
    figura.tight_layout()
    caminho = PASTA / f"{rotulo}_boxplots.svg"
    figura.savefig(caminho)
    plt.close(figura)
    return caminho


def latencia(todas, rotulo) -> Path:
    total = _valores(todas, "latencia_total_s")
    modelo = _valores(todas, "saida.latencia_modelo_s")
    figura, (esq, dir_) = plt.subplots(1, 2, figsize=(11, 3.6), facecolor="white")
    if len(total):
        esq.hist(total, bins=20, color=COR_LOTE, edgecolor="white", linewidth=1)
    _estilo(esq, f"latência total do /extrair, todos os status (n={len(total)}), em s")
    dir_.boxplot([modelo, total], tick_labels=["só o modelo (200)", "total (todos)"], orientation="horizontal", widths=0.5,
                 medianprops={"color": TINTA, "linewidth": 2}, flierprops={"markersize": 3})
    _estilo(dir_, "boxplot da latência (s): a diferença é fila + validação + variáveis")
    figura.tight_layout()
    caminho = PASTA / f"{rotulo}_latencia.svg"
    figura.savefig(caminho)
    plt.close(figura)
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rotulo", default="carga")
    parser.add_argument("--inicio")
    parser.add_argument("--fim")
    args = parser.parse_args()
    filtros = {"inicio": args.inicio, "fim": args.fim, "evento": "resposta"}
    todas = monitor_api.filtrar(filtros)
    ok = [linha for linha in todas if linha.get("status") == "ok"]
    ref = drift.carregar_referencia()
    if not ok:
        sys.exit(f"Nenhuma resposta 200 em {monitoramento.ARQUIVO_LOG} nesse intervalo. Rode a carga antes (just carga 100).")
    resultado = drift.comparar(ok, ref)
    PASTA.mkdir(exist_ok=True)
    n, m = ref["n"], len(ok)
    d_critico = 1.358 * math.sqrt((n + m) / (n * m))  # menor distância KS detectável com alfa 0,05
    status = {}
    for linha in todas:
        status[linha["status"]] = status.get(linha["status"], 0) + 1
    linhas_txt = [
        f"Análise do log '{args.rotulo}' ({monitoramento.ARQUIVO_LOG.name} e rotacionados), intervalo: {args.inicio or 'início'} a {args.fim or 'fim'}",
        f"eventos resposta: {len(todas)} {status}; usados no KS/PSI (status ok): {m}; referência: {n}",
        f"KS: com n_ref={n} e n_lote={m}, só diferenças D >= {d_critico:.3f} são detectáveis com alfa 0,05",
        "",
        f"{'variável':30s} {'média ref':>10s} {'média log':>10s} {'KS D':>7s} {'KS p':>9s} {'PSI':>7s} {'lim.ruído':>9s}  drift?",
    ]
    for nome, v in resultado["variaveis"].items():
        if "erro" in v:
            linhas_txt.append(f"{nome:30s} {v['erro']}")
            continue
        marca = ("KS " if v["ks_drift"] else "") + ("PSI" if v["psi_drift"] else "")
        linhas_txt.append(
            f"{nome:30s} {v['media_ref']:10.4g} {v['media_lote']:10.4g} {v['ks_estatistica']:7.3f} {v['ks_p_valor']:9.3g} "
            f"{v['psi']:7.3f} {v['psi_limite_ruido_95']:9.3f}  {marca or '-'}"
        )
    px = resultado.get("ks_pixels_luminancia")
    if px:
        linhas_txt.append(f"\nKS por pixel (histograma de L somado, como na aula 003): D={px['estatistica']}, p={px['p_valor']}, drift={px['drift']}")
    linhas_txt.append(f"\nCom drift no KS (p < 0,05): {resultado['variaveis_com_drift_ks'] or 'nenhuma'}")
    linhas_txt.append(f"Com drift no KS corrigido por Bonferroni (p < 0,05/{len(resultado['variaveis'])} = {resultado['alfa_bonferroni']:.4f}): "
                      f"{resultado['variaveis_com_drift_ks_bonferroni'] or 'nenhuma'}")
    linhas_txt.append(f"Com drift no PSI (> 0,25 e acima do ruído): {resultado['variaveis_com_drift_psi'] or 'nenhuma'}")
    texto = "\n".join(linhas_txt)
    (PASTA / f"{args.rotulo}_ks_psi.txt").write_text(texto + "\n", encoding="utf-8")
    print(texto)
    for caminho in (histogramas(ok, ref, args.rotulo), boxplots(ok, ref, args.rotulo), latencia(todas, args.rotulo)):
        print("gráfico:", caminho.relative_to(RAIZ))


if __name__ == "__main__":
    main()
