import { Component } from '@angular/core';
import { AuthService } from '../../services/auth.service';
import { Router } from '@angular/router';

@Component({
  selector: 'app-login',
  templateUrl: './login.page.html',
  styleUrls: ['./login.page.scss'],
  standalone:false
})
export class LoginPage {
  email = '';
  password = '';
  loading = false;
  errorMsg = '';

  constructor(private auth: AuthService, private router: Router) {}

  async login() {
    this.loading = true;
    this.errorMsg = '';

    try {
      await this.auth.login(this.email, this.password);
      this.router.navigateByUrl('/home', { replaceUrl: true });
    } catch {
      this.errorMsg = 'Email ou senha inválidos';
    }

    this.loading = false;
  }
}
