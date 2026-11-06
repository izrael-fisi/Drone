//! OS credential store (Windows Credential Manager, macOS Keychain, Linux
//! kernel keyring) for Proxigo session tokens. Nothing here is exposed to the
//! webview directly; `profile.rs` moves tokens in and out on load/save.
//!
//! Windows limits one credential to 2560 bytes (1280 UTF-16 characters), and
//! JWT access tokens are often longer, so values are split across numbered
//! entries: `<name>` holds `chunks:<n>` and `<name>#0..n` hold the parts.

const SERVICE: &str = "Proxigo Desktop";
const CHUNK_CHARS: usize = 1000;
const CHUNK_HEADER: &str = "chunks:";

fn entry(name: &str) -> keyring::Result<keyring::Entry> {
    keyring::Entry::new(SERVICE, name)
}

fn write(name: &str, value: &str) -> Result<(), String> {
    entry(name)
        .and_then(|e| e.set_password(value))
        .map_err(|e| format!("credential store write failed: {e}"))
}

fn read(name: &str) -> Option<String> {
    entry(name).ok()?.get_password().ok()
}

fn remove(name: &str) {
    if let Ok(e) = entry(name) {
        // Missing entries are fine; anything else is not actionable here.
        let _ = e.delete_credential();
    }
}

fn chunk_name(name: &str, index: usize) -> String {
    format!("{name}#{index}")
}

fn split_chunks(value: &str) -> Vec<String> {
    let chars: Vec<char> = value.chars().collect();
    if chars.is_empty() {
        return vec![String::new()];
    }
    chars.chunks(CHUNK_CHARS).map(|chunk| chunk.iter().collect()).collect()
}

pub fn set_secret(name: &str, value: &str) -> Result<(), String> {
    delete_secret(name);
    let chunks = split_chunks(value);
    for (index, chunk) in chunks.iter().enumerate() {
        if let Err(error) = write(&chunk_name(name, index), chunk) {
            delete_secret(name);
            for cleanup in 0..=index {
                remove(&chunk_name(name, cleanup));
            }
            return Err(error);
        }
    }
    // Header last, so a partial write is never read back as a value.
    write(name, &format!("{CHUNK_HEADER}{}", chunks.len()))
}

pub fn get_secret(name: &str) -> Option<String> {
    let header = read(name)?;
    let Some(count) = header.strip_prefix(CHUNK_HEADER).and_then(|n| n.parse::<usize>().ok()) else {
        return Some(header);
    };
    let mut value = String::new();
    for index in 0..count {
        value.push_str(&read(&chunk_name(name, index))?);
    }
    Some(value)
}

pub fn delete_secret(name: &str) {
    if let Some(count) = read(name)
        .and_then(|header| header.strip_prefix(CHUNK_HEADER).and_then(|n| n.parse::<usize>().ok()))
    {
        for index in 0..count {
            remove(&chunk_name(name, index));
        }
    }
    remove(name);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_into_bounded_chunks() {
        let token = "a".repeat(2_345);
        let chunks = split_chunks(&token);
        assert_eq!(chunks.len(), 3);
        assert!(chunks.iter().all(|c| c.chars().count() <= CHUNK_CHARS));
        assert_eq!(chunks.concat(), token);
        assert_eq!(split_chunks(""), vec![String::new()]);
    }
}
