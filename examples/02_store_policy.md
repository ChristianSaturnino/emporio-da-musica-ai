# Store information and policies

Store facts, the withdrawal policy applied to the customer's situation, freight without an invented price, and a defect case where two different rules apply.

> Real conversation with the agent, using `gpt-6-luna`, recorded on 2026-11-02 by `scripts/record_examples.py`. Replies are not edited. The customer's email is replaced by a label. All data is fictional.

**Cliente:** Qual o endereço da loja?

**Assistente:** A loja fica na Rua 14 de Maio, 3200, Centro, Campo Grande - MS, CEP 79202-333.

**Cliente:** Abre domingo?

**Assistente:** Não, aos domingos a loja fica fechada.

**Cliente:** Me arrependi da minha compra, posso devolver meu pedido?

**Assistente:** Você pode solicitar a devolução se a compra foi feita online e ainda está dentro de 7 dias corridos após o recebimento. O produto precisa estar sem uso e na embalagem original, com todos os acessórios e manuais; o frete de devolução é por conta da loja.

A compra foi feita online ou na loja física? E em que data você recebeu o pedido?

**Cliente:** Comprei pelo site e recebi há 5 dias, ainda não usei.

**Assistente:** Pela regra para compras online, e como você recebeu há 5 dias e não usou o produto, parece estar dentro do prazo de 7 dias corridos. Ele também precisa estar na embalagem original, com todos os acessórios e manuais. Fale com nossa equipe para concluir a solicitação; o frete de devolução é por conta da loja.

**Cliente:** Quanto custa entregar em outra cidade?

**Assistente:** O frete para outra cidade é calculado pelo CEP de destino, peso e dimensões do produto. Para instrumentos de grande porte, pode ser necessária uma cotação individual.

**Cliente:** Em outra compra, deu defeito depois de 40 dias. Acabou minha garantia?

**Assistente:** Não dá para afirmar que a garantia acabou sem saber a data em que você recebeu o produto. A política prevê troca por defeito de fabricação em até 30 dias após a compra; depois desse prazo, o atendimento é feito diretamente com o fabricante. Já a garantia legal é de 90 dias a partir do recebimento. Fale com a equipe para avaliarem o seu caso.
