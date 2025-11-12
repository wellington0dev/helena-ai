import { Component, OnInit } from '@angular/core';
import { getAuth, onAuthStateChanged } from 'firebase/auth';
import { UserProfile } from 'src/app/models/user-profile.model';
import { User } from 'src/app/models/user.model';
import { UserService } from 'src/app/services/user.service';

@Component({
  selector: 'app-profile',
  templateUrl: './profile.page.html',
  styleUrls: ['./profile.page.scss'],
  standalone: false,
})
export class ProfilePage implements OnInit {
  user: User | null = null;
  profile: UserProfile = {
    occupation: '',
    birthDate: '',
    phone: '',
    maritalStatus: '',
    extraInfo: ''
  };
  
  loading: boolean = false;
  showSuccess: boolean = false;

  constructor(private userService: UserService) {}

  ngOnInit() {
    const auth = getAuth();
    onAuthStateChanged(auth, (firebaseUser) => {
      if (firebaseUser) {
        this.user = new User(firebaseUser);
        this.userService.getUserProfile(firebaseUser.uid).then(profile => {
          this.profile = profile;
        });
      }
    });
  }

  async saveUserProfile() {
    this.loading = true;
    this.showSuccess = false;

    try {
      await this.userService.saveUserProfile(this.profile);
      this.showSuccess = true;
      
      // Esconder mensagem de sucesso após 3 segundos
      setTimeout(() => {
        this.showSuccess = false;
      }, 3000);
    } catch (error) {
      console.error('Erro ao salvar perfil:', error);
    } finally {
      this.loading = false;
    }
  }
}