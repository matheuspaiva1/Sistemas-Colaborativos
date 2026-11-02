#!/usr/bin/env python3
"""
Figuras da secao de resultados (Fase 5, sem rede).

Le indicadores/indicadores_semestre.csv (gerado por indicadores.py) e
dados_processados/pull_requests.csv (para separar outubro) e grava, em
artigo/resultados/figuras/, um PDF por figura, com um painel por caso:
    - atividade.pdf: PRs integrados por semestre, com a parte de outubro;
    - coordenacao_revisao.pdf: PRs integrados revisados por outro participante
      e integrados pelo proprio autor sem revisao;
    - comunicacao_resposta.pdf: issues e PRs com resposta de outra pessoa.

Pontos vazados = semestre com menos de MIN_ITENS itens no denominador;
* = semestre parcial; linha pontilhada = automacao do agenda (na legenda do
LaTeX).

COMO USAR
    pip install matplotlib
    python graficos.py
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter

# ---------------------------------------------------------------------------
# CONFIGURACAO
# ---------------------------------------------------------------------------
INDICADORES = Path("indicadores") / "indicadores_semestre.csv"
PRS = Path("dados_processados") / "pull_requests.csv"
SAIDA = Path("artigo") / "resultados" / "figuras"

CASOS = [  # ordem da Tabela 2
    ("agenda-tech-brasil/agenda-tech-brasil", "agenda-tech-brasil"),
    ("okfn-brasil/querido-diario", "querido-diario"),
    ("filipedeschamps/tabnews.com.br", "tabnews.com.br"),
    ("BrasilAPI/BrasilAPI", "BrasilAPI"),
    ("brazilian-utils/python", "brutils-python"),
]
SEMESTRES = ["2022.2", "2023.1", "2023.2", "2024.1", "2024.2",
             "2025.1", "2025.2", "2026.1", "2026.2"]
PARCIAIS = {"2022.2", "2026.2"}
MARCO_AGENDA = 4.5  # entre 2024.2 e 2025.1 no eixo x (automacao em 29/11/2024)
MIN_ITENS = 10

# Paleta categorica de referencia (slots 1 e 2), validada para fundo claro.
COR_1, COR_2 = "#2a78d6", "#eb6834"
TEXTO, TEXTO_2, GRADE, FUNDO = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"

plt.rcParams.update({
    "font.size": 7, "axes.titlesize": 7.5, "axes.labelsize": 7,
    "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 7,
    "text.color": TEXTO, "axes.labelcolor": TEXTO_2, "axes.edgecolor": GRADE,
    "xtick.color": TEXTO_2, "ytick.color": TEXTO_2,
    "figure.facecolor": FUNDO, "axes.facecolor": FUNDO,
    "pdf.fonttype": 42,
})

csv.field_size_limit(10**9)


# ---------------------------------------------------------------------------
# DADOS
# ---------------------------------------------------------------------------
def ler(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def numero(valor):
    return float(valor) if valor != "" else None


def serie(linhas, repo, tipo, campo, denominador):
    """(valores, denominadores) por semestre; None onde nao ha item."""
    por_sem = {l["semestre"]: l for l in linhas if l["repo"] == repo and l["tipo"] == tipo}
    valores = [numero(por_sem[s][campo]) if s in por_sem else None for s in SEMESTRES]
    dens = [int(por_sem[s][denominador]) if s in por_sem else 0 for s in SEMESTRES]
    return valores, dens


# ---------------------------------------------------------------------------
# DESENHO
# ---------------------------------------------------------------------------
def grade_de_paineis():
    """Uma faixa de 5 paineis, para caber no limite de paginas do artigo."""
    fig, eixos = plt.subplots(1, 5, figsize=(6.3, 1.9), sharey=True)
    return fig, eixos


def preparar_eixo(ax, titulo, repo):
    ax.set_title(titulo, style="italic", loc="left", pad=3)
    ax.set_xticks(range(len(SEMESTRES)))
    ax.set_xticklabels([s[2:] + ("*" if s in PARCIAIS else "") for s in SEMESTRES],
                       rotation=90)
    ax.tick_params(length=0, pad=2)
    ax.grid(axis="y", color=GRADE, linewidth=0.5)
    ax.set_axisbelow(True)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.set_xlim(-0.6, len(SEMESTRES) - 0.4)
    if repo == CASOS[0][0]:  # adocao da automacao (explicada na legenda da figura)
        ax.axvline(MARCO_AGENDA, color=TEXTO_2, linewidth=0.6, linestyle=":")


def linha(ax, valores, dens, cor, marcador, estilo):
    xs = [i for i, v in enumerate(valores) if v is not None]
    ys = [valores[i] for i in xs]
    ax.plot(xs, ys, color=cor, linewidth=1.3, linestyle=estilo, zorder=2)
    for x, y in zip(xs, ys):
        cheio = dens[x] >= MIN_ITENS
        ax.plot(x, y, marker=marcador, markersize=4.2, color=cor, zorder=3,
                markerfacecolor=cor if cheio else FUNDO, markeredgewidth=1,
                markeredgecolor=cor)


def figura_proporcoes(linhas, nome, series, rotulo_y):
    """series: [(tipo, campo, denominador, legenda, cor, marcador, estilo)]."""
    fig, paineis = grade_de_paineis()
    for ax, (repo, titulo) in zip(paineis, CASOS):
        preparar_eixo(ax, titulo, repo)
        for tipo, campo, den, _, cor, marcador, estilo in series:
            valores, dens = serie(linhas, repo, tipo, campo, den)
            linha(ax, valores, dens, cor, marcador, estilo)
        ax.set_ylim(-0.04, 1.04)
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    paineis[0].set_ylabel(rotulo_y)
    legenda = [Line2D([], [], color=c, marker=m, linestyle=e, linewidth=1.3,
                      markersize=4.2, label=l) for _, _, _, l, c, m, e in series]
    legenda.append(Line2D([], [], color=TEXTO_2, marker="o", linestyle="",
                          markerfacecolor=FUNDO, markersize=4.2,
                          label=f"menos de {MIN_ITENS} itens"))
    rodape(fig, legenda)
    salvar(fig, nome)


def figura_atividade(prs):
    contagem = Counter()
    for p in prs:
        if p["foi_integrado"] == "True" and p["autor_eh_bot"] == "False":
            contagem[(p["repo"], p["semestre"], p["outubro"] == "True")] += 1
    fig, eixos = plt.subplots(1, 5, figsize=(6.3, 1.9))
    paineis = eixos
    for ax, (repo, titulo) in zip(paineis, CASOS):
        preparar_eixo(ax, titulo, repo)
        fora = [contagem[(repo, s, False)] for s in SEMESTRES]
        outubro = [contagem[(repo, s, True)] for s in SEMESTRES]
        x = range(len(SEMESTRES))
        ax.bar(x, fora, width=0.7, color=COR_1, edgecolor=FUNDO, linewidth=0.8)
        ax.bar(x, outubro, width=0.7, bottom=fora, color=COR_2, edgecolor=FUNDO,
               linewidth=0.8, hatch="////")
        ax.set_ylim(0, max(f + o for f, o in zip(fora, outubro)) * 1.1)
    paineis[0].set_ylabel("PRs integrados")
    rodape(fig, [Patch(color=COR_1, label="outros meses"),
                   Patch(facecolor=COR_2, edgecolor=FUNDO, hatch="////",
                         label="outubro")])
    salvar(fig, "atividade.pdf")


def rodape(fig, legenda):
    fig.legend(handles=legenda, loc="upper center", ncol=len(legenda),
               frameon=False, handlelength=2.4, columnspacing=1.4,
               bbox_to_anchor=(0.5, 1.0))


def salvar(fig, nome):
    SAIDA.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(w_pad=0.6, rect=(0, 0, 1, 0.88))
    fig.savefig(SAIDA / nome)
    plt.close(fig)
    print(f"  salvo: {SAIDA / nome}")


def main():
    linhas = ler(INDICADORES)
    figura_atividade(ler(PRS))
    figura_proporcoes(linhas, "coordenacao_revisao.pdf", [
        ("pr", "coo_integrados_prop_revisados_por_outro", "cop_prs_integrados",
         "revisados por outro participante", COR_1, "o", "-"),
        ("pr", "coo_integrados_prop_auto_sem_revisao", "cop_prs_integrados",
         "integrados pelo autor sem revisão", COR_2, "s", "--"),
    ], "% dos PRs integrados")
    figura_proporcoes(linhas, "comunicacao_resposta.pdf", [
        ("issue", "com_prop_com_resposta", "itens", "issues", COR_1, "o", "-"),
        ("pr", "com_prop_com_resposta", "itens", "PRs", COR_2, "s", "--"),
    ], "% com resposta")


if __name__ == "__main__":
    main()
