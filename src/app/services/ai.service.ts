import { Injectable } from '@angular/core';
import { getAI, getGenerativeModel, GoogleAIBackend } from "firebase/ai";
import { app } from "../firebase";
import { getDatabase, ref, push, set, get, query, orderByChild } from "firebase/database";
import { v4 as uuidv4 } from 'uuid';
import { getAuth, onAuthStateChanged } from 'firebase/auth';
import { Message, TypeMessage } from '../models/message.model';
import { MU } from '../utils/message.util';
import { User } from '../models/user.model';

const ai = getAI(app, {
  backend: new GoogleAIBackend()
});

const mu = new MU();

@Injectable({
  providedIn: 'root',
})
export class AiService {

  db = getDatabase(app);
  auth = getAuth();
  user: User | null = null;

  history: History[] = [];
  private readonly HISTORY_LIMIT = 15;

  private model: any | null = null;

  constructor() {
    onAuthStateChanged(this.auth, (user) => {
      if (user) {
        this.user = new User(user);
        console.log(this.user)
        this.model = getGenerativeModel(ai, {
          model: "gemini-2.5-flash",
          systemInstruction: mu.getInstructions(user)
        });
      }
    });
  }

  private getModel() {
    if (!this.model) {
      const user: any = this.auth.currentUser;
      this.model = getGenerativeModel(ai, {
        model: "gemini-2.5-flash-lite",
        systemInstruction: mu.getInstructions(user)
      });
    }
    return this.model;
  }

  async sendUserMessage(text: string): Promise<void> {
    const messageId = uuidv4();
    const uid = this.getUid();
    const msgRef = ref(this.db, `users/${uid}/chat/messages/${messageId}`);

    const message: Message = {
      sender: 'user',
      text: text.trim(),
      type: TypeMessage.TEXT,
      datetime: Date.now()
    }

    await set(msgRef, message);

    await this.generateAiResponse(message);
  }

  private async generateAiResponse(userMessage: Message): Promise<void> {
    const uid = this.getUid();

    try {
      const allMessages = await this.getMessages();
      const recentMessages = this.getLastMessages(allMessages, this.HISTORY_LIMIT);
      const context = await this.getContext();

      const systemPrompt = context
        ? `Contexto resumido da conversa: ${context}`
        : "";

      const fullPrompt = `
${systemPrompt}

Histórico recente da conversa (últimas ${this.HISTORY_LIMIT} mensagens):
${this.historyToText(recentMessages)}

Nova mensagem do usuário: ${JSON.stringify(userMessage)}

Responda de forma natural e contextualizada considerando o histórico acima.
      `.trim();

      const result = await this.getModel().generateContent(fullPrompt);
      const reply = result.response.text();

      const msgId = uuidv4();
      const msgRef = ref(this.db, `users/${uid}/chat/messages/${msgId}`);

      const message:Message = {
        sender: "Helena",
        text: reply,
        type: TypeMessage.TEXT,
        datetime: Date.now()
      }

      await set(msgRef, message);

      if (allMessages.length % 10 === 0) {
        this.generateContext(allMessages);
      }

    } catch (error) {
      console.error('Erro ao gerar resposta da IA:', error);
      throw error;
    }
  }

  private getLastMessages(messages: Message[], limit: number): Message[] {
    return messages.slice(-limit);
  }

  private historyToText(messages: Message[]): string {
    if (messages.length === 0) {
      return "Nenhuma mensagem anterior.";
    }

    return messages.map(msg => {
      const sender = msg.sender === 'user' ? 'Usuário' : 'Helena';
      return `${sender}: ${msg.text}`;
    }).join('\n');
  }

  async getMessages(): Promise<Message[]> {
    const uid = this.getUid();
    if (!uid) return [];

    const chatRef = ref(this.db, `users/${uid}/chat/messages`);
    const orderedQuery = query(chatRef, orderByChild('datetime'));

    try {
      const snapshot = await get(chatRef);
      const data = snapshot.val() || {};

      const messages = Object.values(data) as Message[];
      return messages.sort((a: Message, b: Message) => a.datetime - b.datetime);

    } catch (error) {
      console.error('Erro ao buscar mensagens:', error);
      return [];
    }
  }

  getUid(): string {
    return this.auth.currentUser?.uid || '';
  }

  async saveContext(summary: string) {
    const uid = this.getUid();
    const ctxRef = ref(this.db, `users/${uid}/chat/context`);

    await set(ctxRef, {
      summary,
      updatedAt: Date.now(),
    });
  }

  async getContext(): Promise<string | null> {
    const uid = this.getUid();
    const ctxRef = ref(this.db, `users/${uid}/chat/context`);

    try {
      const snapshot = await get(ctxRef);
      const data = snapshot.val();
      return data?.summary || null;
    } catch (error) {
      console.error('Erro ao buscar contexto:', error);
      return null;
    }
  }

  async generateContext(messages: Message[]) {
    // Usa as últimas 20 mensagens para gerar contexto
    const lastMessages = this.getLastMessages(messages, 20);

    const historyText = this.historyToText(lastMessages);

    const oldContext = await this.getContext();

    const prompt = `
Atualize o resumo do contexto dessa conversa com base nas últimas mensagens.
- Seja conciso (máximo 150 palavras)
- Capture preferências, interesses, tom e intenção do usuário
- Não invente informações
- Foque nos tópicos principais discutidos

CONTEXTO ATUAL:
${oldContext || "Nenhum contexto ainda"}

ÚLTIMAS MENSAGENS:
${historyText}

Resumo atualizado do contexto:
    `.trim();

    try {
      const result = await this.getModel().generateContent(prompt);
      const summary = result.response.text().trim();

      await this.saveContext(summary);
      return summary;
    } catch (error) {
      console.error('Erro ao gerar contexto:', error);
      return oldContext;
    }
  }

  // Método para obter estatísticas do chat (opcional)
  getChatStats(messages: Message[]) {
    const userMessages = messages.filter(msg => msg.sender === this.user?.name).length;
    const assistantMessages = messages.filter(msg => msg.sender === 'Helena').length;
    const totalMessages = messages.length;

    return {
      userMessages,
      assistantMessages,
      totalMessages,
      historyLimit: this.HISTORY_LIMIT
    };
  }

  async clearChat(): Promise<void> {
    const uid = this.getUid();
    const chatRef = ref(this.db, `users/${uid}/chat/messages`);
    const contextRef = ref(this.db, `users/${uid}/chat/context`);

    try {
      await set(chatRef, null);
      await set(contextRef, null);
    } catch (error) {
      console.error('Erro ao limpar chat:', error);
      throw error;
    }
  }
}