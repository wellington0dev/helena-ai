import type { User as UserEntity } from "firebase/auth";
import { UserProfile } from "./user-profile.model";

export class User {
  uid: string;
  name: string;
  email: string;

  constructor(userEntity: UserEntity, profile?: UserProfile) {
    if (!userEntity.uid) {
      throw new Error("Usuário sem uid.")
    }

    if (!userEntity.displayName) {
      throw new Error("Usuário sem displayName. Configure o nome no perfil antes de continuar.");
    }

    if (!userEntity.email) {
      throw new Error("Usuário sem email. Login inválido.");
    }

    this.uid = userEntity.uid;
    this.name = userEntity.displayName;
    this.email = userEntity.email;
  }
}
