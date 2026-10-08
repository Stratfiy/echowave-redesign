// Background tasks must be defined when the JavaScript bundle loads, before
// any screen renders, so the operating system can wake the app to run them
// (expo-task-manager). Then Expo Router takes over.
import './src/lib/background/tasks';
import 'expo-router/entry';
