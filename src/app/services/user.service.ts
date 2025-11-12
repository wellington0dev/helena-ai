import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { DatabaseService } from './database.service';
import { UserProfile } from '../models/user-profile.model';
import { getAuth, onAuthStateChanged } from 'firebase/auth';
import { User } from '../models/user.model';

@Injectable({
  providedIn: 'root',
})
export class UserService {
  private basePath = 'users';
  user: User | null = null;

  constructor(private db: DatabaseService) {
    const auth = getAuth();
    onAuthStateChanged(auth, (firebaseUser) => {
      if (firebaseUser) {
        this.user = new User(firebaseUser);
      }
    });
  }

  async saveUserProfile(profile: UserProfile) {
    if (!this.user) throw new Error("Usuário não autenticado");
    return this.db.writeData(`${this.basePath}/${this.user.uid}/profile`, profile, "");
  }

  async updateUserProfile(profile: Partial<UserProfile>) {
    if (!this.user) throw new Error("Usuário não autenticado");
    return this.db.updateData(`${this.basePath}/${this.user.uid}/profile`, "", profile);
  }

  getUserProfile(uid: string) {
    return this.db.readData(`${this.basePath}/${uid}`, "profile")
      .then((res: UserProfile) => {
        console.log('Perfil:', res);
        return res;
      });
  }


}
