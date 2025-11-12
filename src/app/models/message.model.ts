export interface Message {
  id?: string;
  role: string;
  text: string;
  type: TypeMessage;
  datetime: number;
  referenceMessageId?: string;
}

export enum TypeMessage {
  TEXT = 'text',
  IMAGE = 'image',
  GEN = 'gen',
  SEARCH = 'search'
}

export interface Context {
  summary: string;
  createdAt: string;
  updatedAt: string;
}

export interface History {
  role: string;
  parts: [
    {
      text: string;
    }
  ]
}
