from flask import request, jsonify
from decimal import Decimal, InvalidOperation
from datetime import datetime


def instalar(app):
    if getattr(app, "_pending_extension_instalada", False):
        return
    app._pending_extension_instalada = True

    def banco():
        return app.conectar_banco()

    def uid():
        atual = app.usuario_atual()
        return atual["id"] if atual else None

    def garantir_tabela():
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS vendas_pendentes (
                        id SERIAL PRIMARY KEY,
                        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                        caixa_id INTEGER REFERENCES caixas(id) ON DELETE CASCADE,
                        total NUMERIC(12,2) NOT NULL,
                        pagamento VARCHAR(30) NOT NULL DEFAULT 'dinheiro',
                        descricao VARCHAR(255),
                        criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        atualizada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_pendentes_caixa ON vendas_pendentes(caixa_id, criada_em DESC)")
            conexao.commit()
        finally:
            conexao.close()

    garantir_tabela()

    def autorizado():
        return uid() is not None

    @app.route("/api/vendas-pendentes", methods=["GET"])
    def listar_vendas_pendentes_compartilhadas():
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        usuario_id = uid()
        caixa = app.garantir_caixa(usuario_id)
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""
                    SELECT vp.id, vp.total, vp.pagamento, vp.descricao, vp.criada_em,
                           u.usuario, c.nome AS caixa
                    FROM vendas_pendentes vp
                    LEFT JOIN usuarios u ON u.id = vp.usuario_id
                    LEFT JOIN caixas c ON c.id = vp.caixa_id
                    WHERE vp.caixa_id = %s
                    ORDER BY vp.criada_em DESC
                """, (caixa["id"],))
                rows = cursor.fetchall()
        finally:
            conexao.close()
        resultado = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("total"), Decimal):
                item["total"] = float(item["total"])
            if isinstance(item.get("criada_em"), datetime):
                item["criada_em"] = item["criada_em"].strftime("%d/%m/%Y %H:%M")
            resultado.append(item)
        return jsonify({"pendentes": resultado})

    @app.route("/api/vendas-pendentes", methods=["POST"])
    def criar_venda_pendente_compartilhada():
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        dados = request.get_json(silent=True) or request.form
        try:
            total = Decimal(str(dados.get("total", "0")).replace(",", "."))
            if total <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            return jsonify({"erro": "Informe um valor válido."}), 400
        pagamento = str(dados.get("pagamento", "dinheiro")).strip() or "dinheiro"
        descricao = str(dados.get("descricao", "")).strip()[:255]
        usuario_id = uid()
        caixa = app.garantir_caixa(usuario_id)
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO vendas_pendentes (usuario_id, caixa_id, total, pagamento, descricao)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, total, pagamento, descricao, criada_em
                """, (usuario_id, caixa["id"], total, pagamento, descricao))
                row = cursor.fetchone()
            conexao.commit()
        finally:
            conexao.close()
        item = dict(row)
        item["total"] = float(item["total"])
        if isinstance(item.get("criada_em"), datetime):
            item["criada_em"] = item["criada_em"].strftime("%d/%m/%Y %H:%M")
        return jsonify({"ok": True, "pendente": item}), 201

    @app.route("/api/vendas-pendentes/<int:pendente_id>/continuar", methods=["POST"])
    def continuar_venda_pendente_compartilhada(pendente_id):
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        usuario_id = uid()
        caixa = app.garantir_caixa(usuario_id)
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""
                    SELECT id, total, pagamento, descricao
                    FROM vendas_pendentes
                    WHERE id=%s AND caixa_id=%s
                """, (pendente_id, caixa["id"]))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"erro": "Venda pendente não encontrada neste caixa."}), 404
                cursor.execute("DELETE FROM vendas_pendentes WHERE id=%s AND caixa_id=%s", (pendente_id, caixa["id"]))
            conexao.commit()
        finally:
            conexao.close()
        item = dict(row)
        item["total"] = float(item["total"])
        return jsonify({"ok": True, "pendente": item})

    @app.route("/api/vendas-pendentes/<int:pendente_id>", methods=["DELETE"])
    def excluir_venda_pendente_compartilhada(pendente_id):
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        caixa = app.garantir_caixa(uid())
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("DELETE FROM vendas_pendentes WHERE id=%s AND caixa_id=%s", (pendente_id, caixa["id"]))
                apagadas = cursor.rowcount
            conexao.commit()
        finally:
            conexao.close()
        if not apagadas:
            return jsonify({"erro": "Venda pendente não encontrada neste caixa."}), 404
        return jsonify({"ok": True})
