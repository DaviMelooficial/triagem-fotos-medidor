"""Análise do rastro: KS e PSI de cada variável contra a referência, histogramas e boxplots em SVG.

Lê o arquivo de log direto (inclusive os rotacionados), sem precisar do serviço rodando.
Uso:
  uv run python monitoring/analise.py <carga_id>              (o rótulo que o gerar_carga.py imprime)
  uv run python monitoring/analise.py --inicio 2026-11-16T20:00 [--fim ...] [--rotulo nome]
  (ou: just analise <carga_id>)
Saída em monitoring/saida/ (fora do git): <rotulo>_ks_psi.txt, _histogramas.svg, _boxplots.svg, _latencia.svg.
Os resultados versionados da entrega estão em monitoring/analise/ (gerados com --saida monitoring/analise).
"""

import argparse
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

PASTA = Path(__file__).resolve().parent / "saida"  # fora do git; os resultados da entrega estão em analise/
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


def _resumo_categorico(v: dict) -> str:
    """Ex.: 0.33: 4% -> 12% | 1: 70% -> 52% (categorias que mudam mais primeiro)."""
    cats = sorted(set(v["proporcoes_ref"]) | set(v["proporcoes_lote"]),
                  key=lambda c: -abs(v["proporcoes_lote"].get(c, 0) - v["proporcoes_ref"].get(c, 0)))
    return " | ".join(f"{c}: {100 * v['proporcoes_ref'].get(c, 0):.0f}% -> {100 * v['proporcoes_lote'].get(c, 0):.0f}%" for c in cats[:3])


def main() -> None:
    global PASTA
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carga_id", nargs="?", help="só os pedidos com este carga_id (impresso pelo gerar_carga.py)")
    parser.add_argument("--rotulo", help="prefixo dos arquivos de saída (padrão: o carga_id ou 'carga')")
    parser.add_argument("--inicio")
    parser.add_argument("--fim")
    parser.add_argument("--backend", help="padrão: o backend da referência (simulado)")
    parser.add_argument("--saida", type=Path, default=PASTA, help="pasta dos resultados (padrão: monitoring/saida/)")
    args = parser.parse_args()
    PASTA = args.saida
    rotulo = args.rotulo or args.carga_id or "carga"
    ref = drift.carregar_referencia()
    backend = args.backend or ref.get("backend")
    filtros = {"inicio": args.inicio, "fim": args.fim, "evento": "resposta", "carga_id": args.carga_id, "backend": backend}
    todas = monitor_api.filtrar(filtros)
    ok = [linha for linha in todas if linha.get("status") == "ok"]
    if not ok:
        sys.exit(f"Nenhuma resposta 200 do backend {backend} em {monitoramento.ARQUIVO_LOG} com esse filtro. "
                 "Rode a carga antes (just carga 100).")
    resultado = drift.comparar(ok, ref)
    PASTA.mkdir(parents=True, exist_ok=True)
    n, m = ref["n"], len(ok)
    status = {}
    for linha in todas:
        status[linha["status"]] = status.get(linha["status"], 0) + 1
    filtro_txt = f"carga_id={args.carga_id}" if args.carga_id else f"intervalo {args.inicio or 'início'} a {args.fim or 'fim'}"
    alfa_b = resultado["alfa_bonferroni"]
    linhas_txt = [
        f"Análise do log ({monitoramento.ARQUIVO_LOG.name} e rotacionados), {filtro_txt}, backend {backend}",
        f"eventos resposta: {len(todas)} {status}; usados nos testes (status ok): {m}; referência: {n}",
        f"{resultado['n_testes']} testes ({len(drift.VARIAVEIS)} variáveis + KS por pixel); alfa 0,05; Bonferroni: p < 0,05/{resultado['n_testes']} = {alfa_b:.4f}",
        f"KS: com estes n, D precisa passar de {resultado['ks_d_critico']} para dar p < 0,05 e de "
        f"{resultado['ks_d_critico_bonferroni']} com Bonferroni (valor crítico, não é o poder do teste)",
        "",
        "Contínuas (KS):",
        f"{'variável':30s} {'média ref':>10s} {'média log':>10s} {'KS D':>7s} {'p':>9s} {'PSI':>7s} {'lim.ruído':>9s}  drift?",
    ]

    def marca(v):
        return ("teste" + ("+Bonf " if v["drift_bonferroni"] else " ") if v["drift"] else "") + ("PSI" if v["psi_drift"] else "") or "-"

    for nome in drift.CONTINUAS:
        v = resultado["variaveis"][nome]
        if "erro" in v:
            linhas_txt.append(f"{nome:30s} {v['erro']}")
            continue
        linhas_txt.append(
            f"{nome:30s} {v['media_ref']:10.4g} {v['media_lote']:10.4g} {v['estatistica']:7.3f} {v['p_valor']:9.3g} "
            f"{v['psi']:7.3f} {v['psi_limite_ruido_95']:9.3f}  {marca(v)}"
        )
    linhas_txt += ["", "Discretas/categóricas (qui-quadrado; Fisher se esperado < 5):",
                   f"{'variável':32s} {'teste':>17s} {'p':>9s} {'PSI':>7s} {'lim.ruído':>9s}  drift?  maiores mudanças (ref -> log)"]
    for nome in drift.CATEGORICAS:
        v = resultado["variaveis"][nome]
        if "erro" in v:
            linhas_txt.append(f"{nome:32s} {v['erro']}")
            continue
        linhas_txt.append(f"{nome:32s} {v['teste']:>17s} {v['p_valor']:9.3g} {v['psi']:7.3f} {v['psi_limite_ruido_95']:9.3f}  "
                          f"{marca(v):6s}  {_resumo_categorico(v)}")
    revisao = resultado["variaveis"].get("saida.precisa_revisao", {}).get("diferenca")
    if revisao:
        linhas_txt.append(f"\nprecisa_revisao (KPI): {100 * revisao['fracao_ref']:.1f}% na referência -> {100 * revisao['fracao_lote']:.1f}% no log; "
                          f"diferença {revisao['diferenca_pp']:+.1f} pp, IC 95% [{revisao['ic95_pp'][0]:+.1f}; {revisao['ic95_pp'][1]:+.1f}] pp")
    px = resultado.get("ks_pixels_luminancia")
    if px:
        linhas_txt.append(f"KS por pixel (histograma de L somado, como na aula 003): D={px['estatistica']}, p={px['p_valor']}, drift={px['drift']}")
    linhas_txt.append(f"\nCom drift (p < 0,05): {resultado['variaveis_com_drift'] or 'nenhuma'}")
    linhas_txt.append(f"Com drift após Bonferroni (p < {alfa_b:.4f}): {resultado['variaveis_com_drift_bonferroni'] or 'nenhuma'}")
    linhas_txt.append(f"Com drift no PSI (> 0,25 e acima do ruído; sem correção de multiplicidade): {resultado['variaveis_com_drift_psi'] or 'nenhuma'}")
    texto = "\n".join(linhas_txt)
    (PASTA / f"{rotulo}_ks_psi.txt").write_text(texto + "\n", encoding="utf-8")
    print(texto)
    for caminho in (histogramas(ok, ref, rotulo), boxplots(ok, ref, rotulo), latencia(todas, rotulo)):
        print("gráfico:", caminho)


if __name__ == "__main__":
    main()
