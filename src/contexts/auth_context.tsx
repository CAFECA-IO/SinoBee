'use client';

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';
import { request } from '@/lib/utils/request';
import { publicClient } from '@/lib/viem_public';
import { ABIS, CONTRACT_ADDRESSES } from '@/config/contracts';
import { formatUnits } from 'viem';

interface IUser {
  address: string;
  name: string | null;
  role: string | null;
  plan?: string;
  credits?: number;
  isAdmin?: boolean;
  modules?: string[];
  identityAddress?: string | null;
  isVerified?: boolean;
  pubKeyX?: string;
  pubKeyY?: string;
}

interface IAuthContextType {
  user: IUser | null;
  loading: boolean;
  refreshAuth: () => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<IAuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<IUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshAuth = useCallback(async () => {
    const token = localStorage.getItem('dewt');
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }

    try {
      const response = await request<{ payload: IUser }>('/api/v1/auth/me', {
        method: 'GET',
      });
      if (response && response.payload) {
        let userData = response.payload;
        try {
          if (userData.address) {
            // Info: (20260129 - Tzuhan) Fetch credits from blockchain
            const balance = await publicClient.readContract({
              address: CONTRACT_ADDRESSES.NTD_TOKEN,
              abi: ABIS.NTD_TOKEN,
              functionName: 'balanceOf',
              args: [userData.address as `0x${string}`]
            });
            const credits = Number(formatUnits(balance, 18));

            // Info: (20260129 - Tzuhan) Fetch verification status from IdentityRegistry
            const isVerified = await publicClient.readContract({
              address: CONTRACT_ADDRESSES.IDENTITY_REGISTRY,
              abi: ABIS.IDENTITY_REGISTRY,
              functionName: 'isVerified',
              args: [userData.address as `0x${string}`]
            });

            userData = { ...userData, credits, isVerified };
          }
        } catch (e) {
          console.warn('Failed to fetch user balance:', e);
        }
        setUser(userData);
      } else {
        setUser(null);
        localStorage.removeItem('dewt');
      }
    } catch (error) {
      console.error('Failed to fetch user:', error);
      setUser(null);
      localStorage.removeItem('dewt');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  const logout = useCallback(() => {
    localStorage.removeItem('dewt');
    setUser(null);
    window.location.reload();
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      refreshAuth,
      logout,
    }),
    [user, loading, refreshAuth, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
