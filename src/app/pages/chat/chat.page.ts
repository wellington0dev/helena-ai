import { Component, OnInit, ViewChild, OnDestroy } from '@angular/core';
import { getAuth, onAuthStateChanged } from 'firebase/auth';
import { AiService } from 'src/app/services/ai.service';
import { IonContent } from '@ionic/angular';
import { Message, TypeMessage } from 'src/app/models/message.model';
import { User } from 'src/app/models/user.model';

@Component({
  selector: 'app-chat',
  standalone: false,
  templateUrl: './chat.page.html',
  styleUrls: ['./chat.page.scss'],
})
export class ChatPage implements OnInit, OnDestroy {
  @ViewChild(IonContent, { static: false }) content!: IonContent;

  user: User | null = null;

  messages: Message[] = [];
  inputText: string = '';
  loading: boolean = false;
  showScrollButton: boolean = false;
  private authUnsubscribe: any;
  private scrollTimeout: any;

  constructor(private ai: AiService) { }

  async ngOnInit() {
    const auth = getAuth();

    this.authUnsubscribe = onAuthStateChanged(auth, async (user) => {
      if (user) {
        this.user = new User(user);
        await this.loadMessages();
      }
    });
  }

  async loadMessages() {
    try {
      this.messages = await this.ai.getMessages();
      this.scrollToBottom();
    } catch (error) {
      console.error('Erro ao carregar mensagens:', error);
    }
  }

  ngOnDestroy() {
    if (this.authUnsubscribe) {
      this.authUnsubscribe();
    }
    if (this.scrollTimeout) {
      clearTimeout(this.scrollTimeout);
    }
  }

  async sendMessage() {
    if (!this.inputText.trim() || this.loading) return;

    const userMessage = this.inputText.trim();
    this.loading = true;

    try {
      const userMsg: Message = {
        sender: this.user?.name || "user",
        text: userMessage,
        type: TypeMessage.TEXT,
        datetime: Date.now()
      };
      this.messages.push(userMsg);
      this.inputText = '';
      this.scrollToBottom();

      await this.ai.sendUserMessage(userMessage);

      await this.loadMessages();

    } catch (error) {
      console.error('Erro ao enviar mensagem:', error);
    } finally {
      this.loading = false;
    }
  }

  scrollToBottom() {
    if (this.content) {
      setTimeout(() => {
        this.content.scrollToBottom(300);
        this.showScrollButton = false;
      }, 100);
    }
  }

  onScroll(event: any) {
    const scrollElement = event.target;
    const scrollHeight = scrollElement.scrollHeight;
    const scrollTop = scrollElement.scrollTop;
    const clientHeight = scrollElement.clientHeight;

    // Mostrar botão de scroll se não estiver no final
    this.showScrollButton = scrollHeight - scrollTop - clientHeight > 100;

    // Esconder botão após 3 segundos sem scroll
    clearTimeout(this.scrollTimeout);
    this.scrollTimeout = setTimeout(() => {
      this.showScrollButton = false;
    }, 3000);
  }

  getMessageTime(index: number): string {
    const message = this.messages[index];
    if (message && message.datetime) {
      return new Date(message.datetime).toLocaleTimeString('pt-BR', {
        hour: '2-digit',
        minute: '2-digit'
      });
    }

    // Fallback: simular horário baseado na posição
    const now = new Date();
    const minutesAgo = this.messages.length - index;
    const messageTime = new Date(now.getTime() - minutesAgo * 60000);
    return messageTime.toLocaleTimeString('pt-BR', {
      hour: '2-digit',
      minute: '2-digit'
    });
  }

  isNewDay(index: number): boolean {
    if (index === 0) return true;

    const currentMsg = this.messages[index];
    const previousMsg = this.messages[index - 1];

    if (!currentMsg.datetime || !previousMsg.datetime) return false;

    const currentDate = new Date(currentMsg.datetime).toDateString();
    const previousDate = new Date(previousMsg.datetime).toDateString();

    return currentDate !== previousDate;
  }

  getDaySeparator(index: number): string {
    const message = this.messages[index];
    if (!message.datetime) return '';

    const date = new Date(message.datetime);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    if (date.toDateString() === today.toDateString()) {
      return 'Hoje';
    } else if (date.toDateString() === yesterday.toDateString()) {
      return 'Ontem';
    } else {
      return date.toLocaleDateString('pt-BR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
      });
    }
  }

  // Método opcional para limpar o chat
  async clearChat() {
    try {
      await this.ai.clearChat();
      this.messages = [];
    } catch (error) {
      console.error('Erro ao limpar chat:', error);
    }
  }
}