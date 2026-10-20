import type { Metadata } from 'next';
import { LoginScreen } from '@/components/landing/LoginScreen';

export const metadata: Metadata = { title: 'Đăng nhập · VO3 Studio' };

export default function Login() {
  return <LoginScreen />;
}
