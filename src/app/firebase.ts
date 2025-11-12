import { initializeApp } from "firebase/app";

const firebaseConfig = {
  apiKey: "AIzaSyDetJYQCVHHHn3dyLXszBOD2AQCexn4JQw",
  authDomain: "helena-3d3b0.firebaseapp.com",
  databaseURL: "https://helena-3d3b0-default-rtdb.firebaseio.com",
  projectId: "helena-3d3b0",
  storageBucket: "helena-3d3b0.firebasestorage.app",
  messagingSenderId: "1045418250053",
  appId: "1:1045418250053:web:57b07f7b4e20c03d8c782c"
};

export const app = initializeApp(firebaseConfig);