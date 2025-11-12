import { Component } from '@angular/core';
import { Router } from '@angular/router';
import { AuthService } from 'src/app/services/auth.service';

@Component({
  selector: 'app-home',
  templateUrl: 'home.page.html',
  styleUrls: ['home.page.scss'],
  standalone: false,
})
export class HomePage {
  pages:{
    path:string,
    label:string,
    icon:string
  }[] = [
    {path:'chat', label:'Chat', icon:"chatbox-ellipses"},
    {path:'profile',label:'Seu Perfil', icon:"person"},
    {path:'more',label:'Mais', icon:"add-circle"},
  ]

  constructor(
    private router: Router,
    private authService:AuthService
  ) {

    }

    navigate(path:string){
      this.router.navigateByUrl(path);
    }

    logout(){
      this.authService.logout();
    }
}
