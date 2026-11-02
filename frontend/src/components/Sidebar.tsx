'use client';

import React from 'react';
import {
  LayoutDashboard,
  BookOpen,
  Users,
  FileText,
  Package,
  Settings,
  Bell,
  LogOut,
  Sparkles,
} from 'lucide-react';

interface SidebarProps {
  currentTab: string;
  onTabChange: (tab: string) => void;
  shopName?: string;
  userName?: string;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onTabChange,
  shopName = 'Mohit General Store',
  userName = 'Shopkeeper',
}) => {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'ledger', label: 'Digital Khata', icon: BookOpen },
    { id: 'customers', label: 'Customers', icon: Users },
    { id: 'reports', label: 'Reports & PDF', icon: FileText },
    { id: 'inventory', label: 'Inventory', icon: Package },
    { id: 'reminders', label: 'Reminders', icon: Bell },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <aside className="w-64 bg-[#191919] border-r border-white/10 flex flex-col justify-between h-screen fixed left-0 top-0 z-40 select-none">
      {/* Brand & Logo */}
      <div>
        <div className="h-16 px-6 flex items-center gap-3 border-b border-white/10">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-amber-600 to-amber-400 flex items-center justify-center text-white font-black shadow-lg shadow-amber-500/20">
            H
          </div>
          <div>
            <div className="text-base font-extrabold text-white tracking-tight flex items-center gap-1.5">
              Hisab AI
              <Sparkles className="w-3.5 h-3.5 text-amber-400" />
            </div>
            <div className="text-[11px] text-gray-400 truncate max-w-[140px] font-medium">
              {shopName}
            </div>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="p-3 flex flex-col gap-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = currentTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm font-semibold transition-all ${
                  isActive
                    ? 'bg-white/15 text-white shadow-inner font-bold'
                    : 'text-gray-400 hover:text-white hover:bg-white/5'
                }`}
              >
                <Icon className={`w-4 h-4 ${isActive ? 'text-amber-400' : 'text-gray-400'}`} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* User Profile Footer Card */}
      <div className="p-3 border-t border-white/10 bg-[#161616]">
        <div className="flex items-center justify-between p-2.5 rounded-xl bg-[#242427] border border-white/10 shadow-md">
          <div className="overflow-hidden pr-2">
            <div className="text-sm font-bold text-white truncate">{userName}</div>
            <div className="text-[11px] text-gray-400">Shop Owner</div>
          </div>
          <button
            type="button"
            title="Log out"
            onClick={() => {
              if (confirm('Are you sure you want to log out?')) {
                window.location.href = '/api/auth/logout';
              }
            }}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-rose-500/10 hover:bg-rose-600 text-rose-400 hover:text-white border border-rose-500/30 text-xs font-bold transition-all"
          >
            <LogOut className="w-3 h-3" />
            <span>Logout</span>
          </button>
        </div>
      </div>
    </aside>
  );
};
