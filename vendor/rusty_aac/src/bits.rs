//! MSB-first bit reader for AAC bitstreams.

use crate::{Error, Result};

/// Reads bits most-significant-first from a byte slice (the order AAC uses).
pub struct BitReader<'a> {
    data: &'a [u8],
    /// Absolute bit position from the start of `data`.
    pos: usize,
}

impl<'a> BitReader<'a> {
    pub fn new(data: &'a [u8]) -> BitReader<'a> {
        BitReader { data, pos: 0 }
    }

    /// Total bits remaining.
    pub fn bits_left(&self) -> usize {
        (self.data.len() * 8).saturating_sub(self.pos)
    }

    /// Read a single bit.
    pub fn read_bit(&mut self) -> Result<u32> {
        let byte = self.pos / 8;
        if byte >= self.data.len() {
            return Err(Error::invalid("aac: bit reader past end of data"));
        }
        let shift = 7 - (self.pos % 8);
        let bit = (self.data[byte] >> shift) & 1;
        self.pos += 1;
        Ok(bit as u32)
    }

    /// Peek up to 32 bits MSB-first without consuming them. Caller guarantees
    /// `n <= 32` and `n <= bits_left()`. (TenzorPipe patch: fast path only.)
    #[inline]
    pub fn peek_bits_unchecked(&self, n: u32) -> u32 {
        debug_assert!(n <= 32 && n as usize <= self.bits_left());
        if n == 0 {
            return 0;
        }
        let byte = self.pos / 8;
        let mut word = [0u8; 8];
        let avail = (self.data.len() - byte).min(8);
        word[..avail].copy_from_slice(&self.data[byte..byte + avail]);
        let v = u64::from_be_bytes(word) << (self.pos % 8);
        (v >> (64 - n)) as u32
    }

    /// Consume `n` bits. Caller guarantees `n <= bits_left()`.
    #[inline]
    pub fn consume_unchecked(&mut self, n: usize) {
        debug_assert!(n <= self.bits_left());
        self.pos += n;
    }

    /// Read `n` bits (0..=32) into a `u32`, MSB-first.
    pub fn read_bits(&mut self, n: u32) -> Result<u32> {
        if n > 32 {
            return Err(Error::invalid("aac: read_bits > 32"));
        }
        // TenzorPipe patch: whole-word fast path when every bit is available.
        // Short reads take the original bit-by-bit path, so errors are unchanged.
        if n as usize <= self.bits_left() {
            let v = self.peek_bits_unchecked(n);
            self.pos += n as usize;
            return Ok(v);
        }
        let mut v = 0u32;
        for _ in 0..n {
            v = (v << 1) | self.read_bit()?;
        }
        Ok(v)
    }

    /// Read one bit as a bool.
    pub fn read_bool(&mut self) -> Result<bool> {
        Ok(self.read_bit()? != 0)
    }

    /// Skip `n` bits.
    pub fn skip(&mut self, n: usize) -> Result<()> {
        if self.pos + n > self.data.len() * 8 {
            return Err(Error::invalid("aac: skip past end of data"));
        }
        self.pos += n;
        Ok(())
    }

    /// Advance to the next byte boundary.
    pub fn byte_align(&mut self) {
        if self.pos % 8 != 0 {
            self.pos += 8 - (self.pos % 8);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_read_bits_matches_bitwise_reads_everywhere() {
        let data: Vec<u8> = (0..23u32).map(|i| (i.wrapping_mul(0x9E37_79B1) >> 13) as u8).collect();
        for start in 0..data.len() * 8 {
            for n in 0..=32u32 {
                let mut fast = BitReader::new(&data);
                fast.pos = start;
                let mut slow = BitReader::new(&data);
                slow.pos = start;
                let mut expected = Ok(0u32);
                let mut v = 0u32;
                for _ in 0..n {
                    match slow.read_bit() {
                        Ok(b) => v = (v << 1) | b,
                        Err(e) => {
                            expected = Err(e);
                            break;
                        }
                    }
                }
                if expected.is_ok() {
                    expected = Ok(v);
                }
                let got = fast.read_bits(n);
                assert_eq!(got.is_ok(), expected.is_ok(), "start {start} n {n}");
                if let (Ok(a), Ok(b)) = (got, expected) {
                    assert_eq!(a, b, "start {start} n {n}");
                    assert_eq!(fast.pos, slow.pos);
                }
            }
        }
    }

    #[test]
    fn reads_bits_msb_first() {
        // 0b1011_0010, 0b1100_0001
        let mut r = BitReader::new(&[0xB2, 0xC1]);
        assert_eq!(r.read_bits(4).unwrap(), 0b1011);
        assert_eq!(r.read_bits(4).unwrap(), 0b0010);
        assert_eq!(r.read_bits(3).unwrap(), 0b110);
        assert_eq!(r.read_bit().unwrap(), 0);
        assert_eq!(r.read_bits(4).unwrap(), 0b0001);
        assert_eq!(r.bits_left(), 0);
    }

    #[test]
    fn skip_and_align() {
        let mut r = BitReader::new(&[0xFF, 0x0F]);
        r.skip(4).unwrap();
        r.byte_align(); // jump to bit 8
        assert_eq!(r.read_bits(4).unwrap(), 0x0);
        assert_eq!(r.read_bits(4).unwrap(), 0xF);
    }

    #[test]
    fn errors_past_end() {
        let mut r = BitReader::new(&[0x00]);
        assert!(r.read_bits(8).is_ok());
        assert!(r.read_bit().is_err());
    }
}
