//! Generic canonical-prefix Huffman decoder for the AAC spectral and
//! scalefactor codebooks (ISO 14496-3 §4.A.3).
//!
//! Each codebook is two parallel `'static` arrays — `codes[i]` is the codeword
//! and `lens[i]` its bit length — exactly the form the spec/reference tables
//! ship in. Decoding reads bits MSB-first, accumulating until a `(len, code)`
//! pair matches; the codes are prefix-free, so the first match is the symbol.
//! `decode` returns the array index `i`, which the codebook layer unpacks into
//! spectral coefficients. O(maxlen·count) per codeword — codebooks are small,
//! so it is plenty fast and trivially verifiable.

#![allow(dead_code)]

use crate::{Error, Result};

use crate::bits::BitReader;
use std::sync::OnceLock;

/// Longest prefix resolved by the lookup table. Longer or invalid codewords use
/// the original bit-by-bit scan, so their results and errors are unchanged.
const LUT_BITS: u8 = 11;

/// A Huffman codebook: parallel codeword / bit-length tables.
pub struct HuffBook {
    codes: &'static [u32],
    lens: &'static [u8],
    max_len: u8,
    /// TenzorPipe patch: lazily built `(len << 16) | index` table, 0 = slow path.
    lut: OnceLock<Box<[u32]>>,
}

impl HuffBook {
    pub const fn new(codes: &'static [u32], lens: &'static [u8]) -> HuffBook {
        let mut max = 0u8;
        let mut i = 0;
        while i < lens.len() {
            if lens[i] > max {
                max = lens[i];
            }
            i += 1;
        }
        HuffBook {
            codes,
            lens,
            max_len: max,
            lut: OnceLock::new(),
        }
    }

    fn lut_bits(&self) -> u8 {
        self.max_len.min(LUT_BITS)
    }

    /// Table equivalent of the scan below: for every `bits`-wide prefix, the
    /// shortest length with a matching codeword and, within it, the lowest index.
    fn build_lut(&self) -> Box<[u32]> {
        let bits = self.lut_bits();
        let mut lut = vec![0u32; 1usize << bits].into_boxed_slice();
        for len in 1..=bits {
            for i in 0..self.codes.len() {
                let code = self.codes[i];
                if self.lens[i] != len || (code as u64) >= (1u64 << len) {
                    continue;
                }
                let shift = bits - len;
                let begin = (code as usize) << shift;
                let end = (code as usize + 1) << shift;
                for slot in &mut lut[begin..end] {
                    if *slot == 0 {
                        *slot = ((len as u32) << 16) | i as u32;
                    }
                }
            }
        }
        lut
    }

    pub fn count(&self) -> usize {
        self.codes.len()
    }

    /// The `(codeword, bit-length)` for symbol index `i` — the encoder side.
    pub fn code(&self, i: usize) -> (u32, u8) {
        (self.codes[i], self.lens[i])
    }

    /// Decode the next codeword, returning its symbol index.
    pub fn decode(&self, r: &mut BitReader) -> Result<u16> {
        let bits = self.lut_bits();
        if bits > 0 && r.bits_left() >= bits as usize {
            let lut = self.lut.get_or_init(|| self.build_lut());
            let entry = lut[r.peek_bits_unchecked(bits as u32) as usize];
            if entry != 0 {
                r.consume_unchecked((entry >> 16) as usize);
                return Ok(entry as u16);
            }
        }
        self.decode_scan(r)
    }

    /// The original O(maxlen·count) decoder (also the reference for tests).
    pub fn decode_scan(&self, r: &mut BitReader) -> Result<u16> {
        let mut code = 0u32;
        for len in 1..=self.max_len {
            code = (code << 1) | r.read_bit()?;
            for i in 0..self.codes.len() {
                if self.lens[i] == len && self.codes[i] == code {
                    return Ok(i as u16);
                }
            }
        }
        Err(Error::invalid("aac: invalid Huffman codeword"))
    }

    /// Kraft sum Σ 2^-len — 1.0 for a complete code, slightly less if incomplete.
    #[cfg(test)]
    pub fn kraft_sum(&self) -> f64 {
        self.lens.iter().map(|&l| 2f64.powi(-(l as i32))).sum()
    }

    /// True if no codeword is a prefix of another.
    #[cfg(test)]
    pub fn is_prefix_free(&self) -> bool {
        for a in 0..self.codes.len() {
            for b in (a + 1)..self.codes.len() {
                let (la, ca) = (self.lens[a], self.codes[a]);
                let (lb, cb) = (self.lens[b], self.codes[b]);
                let (short_l, short_c, long_l, long_c) = if la <= lb {
                    (la, ca, lb, cb)
                } else {
                    (lb, cb, la, ca)
                };
                if long_c >> (long_l - short_l) == short_c {
                    return false;
                }
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // A tiny prefix-free book: index 0→"0", 1→"10", 2→"110", 3→"111".
    static TEST_CODES: &[u32] = &[0b0, 0b10, 0b110, 0b111];
    static TEST_LENS: &[u8] = &[1, 2, 3, 3];

    #[test]
    fn table_decode_matches_scan_for_every_book_on_random_and_truncated_streams() {
        use crate::tables::{SCALEFACTOR_BOOK, SPECTRAL_BOOKS};
        let mut books: Vec<&HuffBook> = SPECTRAL_BOOKS.iter().skip(1).collect();
        books.push(&SCALEFACTOR_BOOK);
        let mut state = 0x2545_F491_4F6C_DD1Du64;
        for book in books {
            // Valid concatenated codewords, then random bytes, each at many lengths.
            let mut valid = Vec::new();
            let (mut acc, mut nbits) = (0u64, 0u32);
            for _ in 0..4000 {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                let (code, len) = book.code((state % book.count() as u64) as usize);
                acc = (acc << len) | code as u64;
                nbits += len as u32;
                while nbits >= 8 {
                    valid.push((acc >> (nbits - 8)) as u8);
                    nbits -= 8;
                }
            }
            let random: Vec<u8> = (0..4000)
                .map(|_| {
                    state ^= state << 13;
                    state ^= state >> 7;
                    state ^= state << 17;
                    state as u8
                })
                .collect();
            for stream in [&valid, &random] {
                for cut in [0usize, 1, 2, 3, 7, 64, stream.len()] {
                    let data = &stream[..cut.min(stream.len())];
                    let mut fast = BitReader::new(data);
                    let mut slow = BitReader::new(data);
                    loop {
                        let a = book.decode(&mut fast);
                        let b = book.decode_scan(&mut slow);
                        assert_eq!(a.is_ok(), b.is_ok());
                        match (a, b) {
                            (Ok(x), Ok(y)) => {
                                assert_eq!(x, y);
                                assert_eq!(fast.bits_left(), slow.bits_left());
                            }
                            _ => break,
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn decodes_prefix_free_sequence() {
        let book = HuffBook::new(TEST_CODES, TEST_LENS);
        // Stream 0 10 110 111 0 → indices 0 1 2 3 0
        // bits: 0 10 110 111 0 → 0101 1011 10.. = 0x5B 0x80
        let mut r = BitReader::new(&[0x5B, 0x80]);
        let got: Vec<u16> = (0..5).map(|_| book.decode(&mut r).unwrap()).collect();
        assert_eq!(got, vec![0, 1, 2, 3, 0]);
    }

    #[test]
    fn structural_helpers() {
        let book = HuffBook::new(TEST_CODES, TEST_LENS);
        assert!((book.kraft_sum() - 1.0).abs() < 1e-12);
        assert!(book.is_prefix_free());
    }
}
