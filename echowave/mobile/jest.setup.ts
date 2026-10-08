// Native modules the libraries import at load time, replaced for Node.
jest.mock('@react-native-async-storage/async-storage', () =>
    require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);
jest.mock('expo-secure-store', () => {
    const data = new Map<string, string>();
    return {
        AFTER_FIRST_UNLOCK: 0,
        getItemAsync: jest.fn(async (k: string) => data.get(k) ?? null),
        setItemAsync: jest.fn(async (k: string, v: string) => void data.set(k, v)),
        deleteItemAsync: jest.fn(async (k: string) => void data.delete(k)),
    };
});
