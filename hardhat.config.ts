import hardhatToolboxViemPlugin from '@nomicfoundation/hardhat-toolbox-viem';
import { configVariable, defineConfig } from 'hardhat/config';
import 'dotenv/config';

export default defineConfig({
  plugins: [hardhatToolboxViemPlugin],
  solidity: {
    profiles: {
      default: {
        version: '0.8.28',
        settings: {
          optimizer: {
            enabled: true,
            runs: 200,
          },
          evmVersion: 'paris',
        },
      },
      production: {
        version: '0.8.28',
        settings: {
          optimizer: {
            enabled: true,
            runs: 200,
          },
          evmVersion: 'paris',
        },
      },
    },
  },
  paths: {
    sources: './contracts',
    tests: './test',
    cache: './cache',
    artifacts: './artifacts',
  },
  networks: {
    isuncoin_mainnet: {
      type: 'http',
      chainType: 'l1',
      url: 'https://mainnet.isuncoin.com',
      accounts: [configVariable('ISUNCOIN_PRIVATE_KEY')],
    },
    localhost: {
      type: 'http',
      url: 'http://127.0.0.1:8545',
    },
  },
});
