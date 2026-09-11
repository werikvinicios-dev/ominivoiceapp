"""Atributos de "voice design" oferecidos na interface.

Os rótulos são traduzidos, mas o valor enviado ao modelo é sempre o termo
que ele reconhece: inglês para gênero/idade/tom/sotaque e chinês para os
dialetos chineses.
"""

from __future__ import annotations

from typing import NamedTuple


class Option(NamedTuple):
    value: str  # termo enviado ao modelo
    label: str  # rótulo exibido


class Category(NamedTuple):
    key: str
    label: str
    options: list[Option]
    hint: str | None = None


CATEGORIES: list[Category] = [
    Category(
        key="gender",
        label="Gênero",
        options=[Option("male", "Masculino"), Option("female", "Feminino")],
    ),
    Category(
        key="age",
        label="Idade",
        options=[
            Option("child", "Criança"),
            Option("teenager", "Adolescente"),
            Option("young adult", "Jovem adulto"),
            Option("middle-aged", "Meia-idade"),
            Option("elderly", "Idoso"),
        ],
    ),
    Category(
        key="pitch",
        label="Tom de voz",
        options=[
            Option("very low pitch", "Muito grave"),
            Option("low pitch", "Grave"),
            Option("moderate pitch", "Médio"),
            Option("high pitch", "Agudo"),
            Option("very high pitch", "Muito agudo"),
        ],
    ),
    Category(
        key="style",
        label="Estilo",
        options=[Option("whisper", "Sussurro")],
    ),
    Category(
        key="accent",
        label="Sotaque em inglês",
        hint="Só tem efeito em textos em inglês.",
        options=[
            Option("american accent", "Americano"),
            Option("british accent", "Britânico"),
            Option("australian accent", "Australiano"),
            Option("canadian accent", "Canadense"),
            Option("indian accent", "Indiano"),
            Option("chinese accent", "Chinês"),
            Option("japanese accent", "Japonês"),
            Option("korean accent", "Coreano"),
            Option("portuguese accent", "Português"),
            Option("russian accent", "Russo"),
        ],
    ),
    Category(
        key="dialect",
        label="Dialeto chinês",
        hint="Só tem efeito em textos em chinês.",
        options=[
            Option("河南话", "Henan"),
            Option("陕西话", "Shaanxi"),
            Option("四川话", "Sichuan"),
            Option("贵州话", "Guizhou"),
            Option("云南话", "Yunnan"),
            Option("桂林话", "Guilin"),
            Option("济南话", "Jinan"),
            Option("石家庄话", "Shijiazhuang"),
            Option("甘肃话", "Gansu"),
            Option("宁夏话", "Ningxia"),
            Option("青岛话", "Qingdao"),
            Option("东北话", "Nordeste"),
        ],
    ),
]

_VALID = {
    category.key: {option.value for option in category.options}
    for category in CATEGORIES
}


def build_instruct(selections: dict[str, str]) -> str | None:
    """Monta o instruct a partir das escolhas do formulário.

    Só passam valores da whitelist: o ``instruct`` do OmniVoice é uma lista
    fechada de atributos e ele levanta ``ValueError`` diante de qualquer termo
    fora dela — descrições livres não funcionariam.
    """
    parts = [
        value
        for category in CATEGORIES
        if (value := (selections.get(category.key) or "").strip())
        and value in _VALID[category.key]
    ]
    return ", ".join(parts) if parts else None
