# 🚛 Frotas-Sys

> Sistema completo de gestão de frota — do Excel bagunçado ao pipeline de dados com PostgreSQL, Flask e SQL analítico.

![Python](https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.1-black?style=flat-square&logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-green?style=flat-square&logo=postgresql)
![Status](https://img.shields.io/badge/status-em%20desenvolvimento-orange?style=flat-square)

---

## 📌 Sobre o Projeto

Esse projeto nasceu de um problema real: uma empresa controlava toda a frota em planilhas Excel sem nenhuma padronização — datas em formatos diferentes, placas escritas de qualquer jeito, valores sem separador consistente.

A solução foi construir um pipeline de dados completo:

```
Excel / PDF / XML (NF-e)
        ↓
  Python (Pandas + pdfplumber + lxml)
        ↓
  Normalização e limpeza de dados
        ↓
  PostgreSQL (Supabase)
        ↓
  Consultas analíticas com Window Functions
        ↓
  Dashboard web (Flask)
```

---

## 🗂️ Arquitetura

```
frotas-sys/
├── app.py              # Rotas Flask e lógica de negócio
├── normalizar.py       # Pipeline de limpeza e normalização de dados
├── leitor_pdf.py       # Parser de NF-e em PDF (Sagui, Paradão, genérico)
├── requirements.txt
└── templates/
    ├── base.html
    ├── index.html       # Dashboard com KPIs
    ├── upload.html      # Ingestão de XML/PDF
    ├── veiculo.html     # Histórico por veículo + gráfico
    ├── relatorios.html  # Relatórios com rankings
    └── ...
```

---

## ⚙️ Stack Técnica

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11 + Flask |
| Banco de dados | PostgreSQL via Supabase |
| Ingestão XML | lxml (parser de NF-e) |
| Ingestão PDF | pdfplumber + OCR (pytesseract) |
| Normalização | Pandas + regex customizado |
| Autenticação | Flask-Login + CSRF |
| Frontend | HTML/CSS/JS + Chart.js |

---

## 🔍 Engenharia de Dados

### Normalização de Dados

O módulo `normalizar.py` resolve os principais problemas encontrados nas planilhas originais:

```python
# Placa em qualquer formato → padrão sem hífen
normalizar_placa("PPW-3444")  # → "PPW3444"
normalizar_placa("qrh 1i14")  # → "QRH1I14"

# Valores com vírgula ou ponto → float consistente
normalizar_numero("1.234,56")  # → 1234.56
normalizar_numero("416,30")    # → 416.30

# Datas em qualquer formato → ISO 8601
normalizar_data("06/05/2026")  # → "2026-05-06"
normalizar_data("06-05-2026")  # → "2026-05-06"
```

### Parser de NF-e

O sistema detecta e processa automaticamente 3 tipos de documentos fiscais:

- **NF-e XML** — parser via lxml com extração de campos do cabeçalho e itens
- **Fatura Sagui** — PDF consolidado com múltiplos abastecimentos por placa
- **Cupom Paradão** — cupom fiscal simples com regex específico

### Modelo Relacional

```sql
veiculos        → placa (PK), modelo, tipo_combustivel, motorista_padrao
postos          → id_posto (PK), nome, endereco
notas_fiscais   → id (PK), numero_nf, data, valor_total, tipo
abastecimentos  → id (PK), placa (FK), id_posto (FK), id_nota (FK), ...
manutencao      → id (PK), placa (FK), id_nota (FK), descricao, valor, ...
```

---

## 📊 Consultas Analíticas (Window Functions)

### Ranking de gasto por veículo/mês

```sql
WITH soma_carro AS (
  SELECT v.placa, v.modelo,
    EXTRACT(MONTH FROM a.data) AS mes,
    EXTRACT(YEAR FROM a.data) AS ano,
    SUM(a.valor_total) AS valor_total_carro
  FROM abastecimentos a
  JOIN veiculos v ON a.placa = v.placa
  GROUP BY v.placa, v.modelo,
    EXTRACT(MONTH FROM a.data),
    EXTRACT(YEAR FROM a.data)
)
SELECT placa, modelo, ano, mes, valor_total_carro,
  DENSE_RANK() OVER (
    PARTITION BY mes, ano
    ORDER BY valor_total_carro DESC
  ) AS ranking
FROM soma_carro;
```

### Variação de preço por produto/posto

```sql
SELECT a.data, a.produto, p.nome,
  a.valor_unitario,
  a.valor_unitario - LAG(valor_unitario) OVER (
    PARTITION BY a.produto, p.nome
    ORDER BY a.data
  ) AS variacao_preco
FROM abastecimentos a
JOIN postos p ON a.id_posto = p.id_posto;
```

### Custo acumulado por veículo

```sql
SELECT a.data, v.placa,
  SUM(a.valor_total) OVER (
    PARTITION BY v.placa
    ORDER BY a.data ASC, a.id_abastecimento ASC
  ) AS custo_acumulado
FROM abastecimentos a
JOIN veiculos v ON v.placa = a.placa;
```

---

## 🖥️ Funcionalidades

- Upload e leitura automática de NF-e (XML e PDF)
- Registro manual de abastecimentos e manutenções
- Dashboard com KPIs por período filtrável
- Histórico completo por veículo com gráfico de custo mensal
- Relatórios com ranking de gastos por veículo, posto e motorista
- Detecção de duplicatas por número de NF

---

## 🖼️ Screenshots

<img width="1106" height="464" alt="image" src="https://github.com/user-attachments/assets/e34dd05b-3c57-49a5-a343-0e61fb747cba" />
<img width="1138" height="616" alt="image" src="https://github.com/user-attachments/assets/eff13e35-71e3-467d-beb6-683dc90d262d" />


---

## 🚀 Como Rodar Localmente

```bash
# Clone o repositório
git clone https://github.com/caduvini12/Frotas-Sys.git
cd Frotas-Sys

# Crie o ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Instale as dependências
pip install -r requirements.txt

# Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env com suas credenciais do Supabase

# Rode o servidor
python app.py
```

### Variáveis de Ambiente

Crie um arquivo `.env` na raiz com:

```env
DB_HOST=seu_host_supabase
DB_NAME=postgres
DB_USER=seu_usuario
DB_PASSWORD=sua_senha
DB_PORT=6543
SECRET_KEY=sua_chave_secreta
ADMIN_USERNAME=seu_usuario_admin
ADMIN_PASSWORD=sua_senha_admin
FLASK_DEBUG=False
```

---


## 👨‍💻 Autor

**Carlos Eduardo** — estudante de Engenharia de Dados  
[GitHub](https://github.com/caduvini12)

---

> Projeto desenvolvido como portfólio prático de Engenharia de Dados — problema real, dados reais, solução real.
