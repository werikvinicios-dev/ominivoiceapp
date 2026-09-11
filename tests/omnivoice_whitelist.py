"""Cópia da whitelist de ``instruct`` do OmniVoice.

Espelha ``_INSTRUCT_CATEGORIES`` em ``omnivoice/utils/voice_design.py``. Existe
para que os testes verifiquem, sem precisar do pacote instalado, que a interface
só oferece atributos que o modelo aceita — ele levanta ``ValueError`` para
qualquer outro termo.
"""

VALID = frozenset(
    {
        "male", "female",
        "child", "teenager", "young adult", "middle-aged", "elderly",
        "very low pitch", "low pitch", "moderate pitch",
        "high pitch", "very high pitch",
        "whisper",
        "american accent", "british accent", "australian accent",
        "chinese accent", "canadian accent", "indian accent",
        "korean accent", "portuguese accent", "russian accent",
        "japanese accent",
        "河南话", "陕西话", "四川话", "贵州话", "云南话", "桂林话",
        "济南话", "石家庄话", "甘肃话", "宁夏话", "青岛话", "东北话",
        # Equivalentes em chinês de gênero/idade/tom/estilo.
        "男", "女", "儿童", "少年", "青年", "中年", "老年",
        "极低音调", "低音调", "中音调", "高音调", "极高音调", "耳语",
    }
)
