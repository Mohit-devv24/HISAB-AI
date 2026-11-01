import { apiRequest } from './api';
import { User } from '../types';

export const AuthService = {
  login: (data: { email: string; password: string }) =>
    apiRequest<{ user: User }>('/api/auth/login', { method: 'POST', body: JSON.stringify(data) }),
  logout: () =>
    apiRequest('/api/auth/logout', { method: 'POST' }),
  getCurrentUser: () =>
    apiRequest<User>('/api/auth/me')
};
