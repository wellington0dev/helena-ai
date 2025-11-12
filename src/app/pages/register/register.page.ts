import { Component } from '@angular/core';
import { AuthService } from '../../services/auth.service';
import { Router } from '@angular/router';

@Component({
  selector: 'app-register',
  templateUrl: './register.page.html',
  styleUrls: ['./register.page.scss'],
  standalone: false
})
export class RegisterPage {
  email = '';
  password = '';
  confirmPassword = '';
  loading = false;
  errorMsg = '';

  constructor(private auth: AuthService, private router: Router) { }

  async register() {
    this.loading = true;
    this.errorMsg = '';

    if (!this.email || !this.password || !this.confirmPassword) {
      this.errorMsg = 'Por favor, preencha todos os campos';
      this.loading = false;
      return;
    }

    if (this.password !== this.confirmPassword) {
      this.errorMsg = 'As senhas não coincidem';
      this.loading = false;
      return;
    }

    if (this.password.length < 6) {
      this.errorMsg = 'A senha deve ter pelo menos 6 caracteres';
      this.loading = false;
      return;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(this.email)) {
      this.errorMsg = 'Por favor, insira um email válido';
      this.loading = false;
      return;
    }

    try {
      await this.auth.register(this.email, this.password);
      this.router.navigateByUrl('/chat', { replaceUrl: true });
    } catch (err: any) {
      this.errorMsg = err.message || 'Erro ao criar conta';
    }

    this.loading = false;
  }

  getPasswordStrength(): string {
    if (!this.password) return '';

    if (this.password.length < 6) return 'weak';
    if (this.password.length < 8) return 'medium';

    const hasLetters = /[a-zA-Z]/.test(this.password);
    const hasNumbers = /[0-9]/.test(this.password);
    const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(this.password);

    if (hasLetters && hasNumbers && hasSpecial) return 'strong';
    if (hasLetters && hasNumbers) return 'medium';

    return 'weak';
  }

  passwordsMatch(): boolean {
    return this.password === this.confirmPassword && this.password.length > 0;
  }

  isFormValid(): boolean {
    return !!this.email &&
      !!this.password &&
      !!this.confirmPassword &&
      this.passwordsMatch() &&
      this.password.length >= 6;
  }
}