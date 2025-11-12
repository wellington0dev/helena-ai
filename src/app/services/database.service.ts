import { Injectable } from '@angular/core';
import { child, get, getDatabase, onValue, ref, set, update } from "firebase/database";
import { app } from '../firebase';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class DatabaseService {
  private database = getDatabase(app);

  writeData(path: string, data: any, id: string) {
    return set(ref(this.database, `${path}/${id}`), data);
  }

  async readData(path: string, id: string): Promise<any> {
    return get(child(ref(this.database), `${path}/${id}`)).then((snapshot) => {
      if (snapshot.exists()) {
        return snapshot.val();
      } else {
        throw new Error("Nenhum dado encontrado");
      }
    });
  }

  updateData(path: string, id: string, data: any) {
    return update(ref(this.database, `${path}/${id}`), data);
  }

  readInRealTime(path: string, id: string): Observable<any> {
    return new Observable((observer) => {
      const dbRef = ref(this.database, `${path}/${id}`);

      onValue(dbRef, (snapshot) => {
        observer.next(snapshot.val());
      }, (error) => {
        observer.error(error);
      });
    });
  }
}
