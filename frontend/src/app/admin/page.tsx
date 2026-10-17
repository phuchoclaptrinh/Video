import type { Metadata } from 'next';
import { AdminConsole } from '@/components/admin/AdminConsole';
import './admin.css';

export const metadata: Metadata = { title: 'Quản trị · VO3 Studio' };

export default function AdminPage() {
  return <AdminConsole />;
}
