import { User } from '@/generated/client';
import { prisma } from '@/lib/prisma';

export interface IWebAuthnRepository {
  findUserByCredentialId(credentialId: string): Promise<User | null>;
  findUserByAddress(address: string): Promise<User | null>;
  findUserById(id: string): Promise<User | null>;
  updateChallenge(address: string, challenge: string): Promise<void>;
  upsertUser(data: {
    address: string;
    pubKeyX: string;
    pubKeyY: string;
    credentialId?: string;
    name?: string;
    imageUrl?: string;
  }): Promise<User>;
}

class WebAuthnRepository implements IWebAuthnRepository {
  public async findUserByCredentialId(credentialId: string): Promise<User | null> {
    return prisma.user.findUnique({
      where: { credentialId },
    });
  }

  public async findUserByAddress(address: string): Promise<User | null> {
    return prisma.user.findUnique({
      where: { address },
    });
  }

  public async findUserById(id: string): Promise<User | null> {
    return prisma.user.findUnique({
      where: { id },
    });
  }

  public async updateChallenge(address: string, challenge: string): Promise<void> {
    await prisma.user.update({
      where: { address },
      data: { currentChallenge: challenge },
    });
  }

  public async updateKYCData(userId: string, data: JSON): Promise<User> {
    return prisma.user.update({
      where: { id: userId },
      data: { kycData: JSON.stringify(data) },
    });
  }

  public async updateIdentityAddress(userId: string, identityAddress: string): Promise<User> {
    return prisma.user.update({
      where: { id: userId },
      data: { identityAddress },
    });
  }

  // Info: (20251223 - Tzuhan) 用於 Indexer 或 Lazy Sync 寫入
  public async upsertUser(data: {
    address: string;
    pubKeyX: string;
    pubKeyY: string;
    credentialId?: string;
    name?: string;
    imageUrl?: string;
  }): Promise<User> {
    return prisma.user.upsert({
      where: { address: data.address },
      update: {
        pubKeyX: data.pubKeyX,
        pubKeyY: data.pubKeyY,
        ...(data.credentialId ? { credentialId: data.credentialId, currentChallenge: null } : {}),
        ...(data.name ? { name: data.name } : {}),
        ...(data.imageUrl ? { imageUrl: data.imageUrl } : {}),
      },
      create: {
        address: data.address,
        pubKeyX: data.pubKeyX,
        pubKeyY: data.pubKeyY,
        credentialId: data.credentialId,
        name: data.name ?? `User ${data.address.slice(0, 6)}`,
        imageUrl: data.imageUrl ?? null,
      },
    });
  }
}

export const webAuthnRepo = new WebAuthnRepository();
