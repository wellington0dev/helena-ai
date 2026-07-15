"""Federação: comunicação servidor-a-servidor entre instâncias da Helena.

Pacote separado de app/agent/ de propósito — isto é infraestrutura de
transporte/confiança, não uma tool da IA. Nas próximas fases (diálogo IA-IA,
compartilhamento de arquivo), o contexto federado da IA vai rodar com um
conjunto de tools próprio e restrito; nada aqui concede acesso a
executar_shell, desktop ou aos dados privados do usuário.
"""
