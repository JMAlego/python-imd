"""Module allowing loading, manipulation, and saving of IMD disk images."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import log2
import re
import struct
from typing import Literal


@dataclass
class Disk:
    """Represents an IMD disk image."""

    version: tuple[int, int]
    date: tuple[int, int, int]
    time: tuple[int, int, int]
    comment: str
    tracks: list[Track]

    _META_DATA = re.compile(rb"^IMD (?P<major>[0-9]).(?P<minor>[0-9]{1,2}): +?"
                            rb"(?P<day>[0-9]{1,2})/(?P<month>[0-9]{1,2})/(?P<year>[0-9]{2,4}) +?"
                            rb"(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{1,2}):(?P<second>[0-9]{1,2})"
                            b"\r\n")
    _META_DATA_FORMAT = "IMD {:01d}.{:02d}: {:02d}/{:02d}/{:04d} {: 2d}:{:02d}:{:02d}\r\n"

    @staticmethod
    def from_file(file_path: str) -> Disk:
        """Load an IMD disk image from file."""
        with open(file_path, "rb") as file:
            imd_data: bytes = file.read()

        return Disk.from_bytes(imd_data)

    @staticmethod
    def from_bytes(imd_data: bytes) -> Disk:
        """Create a Disk from it's binary representation."""
        header_end = imd_data.index(b"\x1A")

        header_data = imd_data[:header_end]
        track_data = imd_data[header_end + 1:]

        meta_match = Disk._META_DATA.match(header_data)
        assert meta_match is not None

        meta_dict = {x: int(y.decode("ascii")) for x, y in meta_match.groupdict().items()}

        version = meta_dict["major"], meta_dict["minor"]
        date = meta_dict["day"], meta_dict["month"], meta_dict["year"]
        time = meta_dict["hour"], meta_dict["minute"], meta_dict["second"]
        comment = header_data[meta_match.end():].decode("ascii")
        tracks = []

        while track_data:
            track = Track.from_bytes(track_data)
            tracks.append(track)
            track_data = track_data[track.get_size():]

        return Disk(version, date, time, comment, tracks)

    def to_bytes(self) -> bytes:
        """Get this Disk's binary representation."""
        disk_data = Disk._META_DATA_FORMAT.format(*self.version, *self.date,
                                                  *self.time).encode("ascii")
        disk_data += self.comment.encode("ascii")

        disk_data += b"\x1A"

        for track in self.tracks:
            disk_data += track.to_bytes()

        return disk_data


class TrackMode(IntEnum):
    """Enum of track mode.

    Modes are a combination of one of three speeds and one of 2 encoding types.
    """

    FM_500KBPS = 0
    FM_300KBPS = 1
    FM_250KBPS = 2
    MFM_500KBPS = 3
    MFM_300KBPS = 4
    MFM_250KBPS = 5


SectorSize = Literal[128, 256, 512, 1024, 2048, 4096, 8192]


@dataclass
class Track:
    """Represents a track on a disk."""

    mode: TrackMode
    cylinder: int
    head: Literal[0, 1]
    sector_count: int
    sector_size: SectorSize
    sector_numbering_map: list
    sector_cylinder_map: list | None
    sector_head_map: list | None
    sector_data_records: list[SectorDataRecord]

    _TRACK_HEADER_FORMAT = "BBBBB"
    _SECTOR_MAP_ENTRY_FORMAT = "{sector_count}B"

    def get_size(self) -> int:
        """Get the size of this Track's binary representation in bytes."""
        data_records = sum(record.get_size() for record in self.sector_data_records)
        sector_numbering_map = self.sector_count
        sector_head_map = self.sector_count if self.sector_head_map else 0
        cylinder_map = self.sector_count if self.sector_cylinder_map else 0
        header_size = struct.calcsize(Track._TRACK_HEADER_FORMAT)
        return data_records + sector_numbering_map + sector_head_map + cylinder_map + header_size

    @staticmethod
    def from_bytes(track_data: bytes) -> Track:
        """Create a Track object from it's binary representation."""
        (mode_value, cylinder, head, sector_count,
         sector_size) = struct.unpack_from(Track._TRACK_HEADER_FORMAT, track_data)
        sector_cylinder_map_present = (head & 0x80) != 0
        sector_head_map_present = (head & 0x40) != 0
        head = head & 0x01
        sector_size = 2**(7 + sector_size)
        mode = TrackMode(mode_value)
        track_data = track_data[struct.calcsize(Track._TRACK_HEADER_FORMAT):]

        sector_map_format = Track._SECTOR_MAP_ENTRY_FORMAT.format(sector_count=sector_count)
        numbering_map = list(struct.unpack_from(sector_map_format, track_data))
        track_data = track_data[struct.calcsize(sector_map_format):]

        sector_cylinder_map = None
        if sector_cylinder_map_present:
            sector_cylinder_map = list(struct.unpack_from(sector_map_format, track_data))
            track_data = track_data[struct.calcsize(sector_map_format):]

        sector_head_map = None
        if sector_head_map_present:
            sector_head_map = list(struct.unpack_from(sector_map_format, track_data))
            track_data = track_data[struct.calcsize(sector_map_format):]

        sector_data_records = []
        for _ in range(sector_count):
            sector_record = SectorDataRecord.from_bytes(track_data, sector_size)
            sector_data_records.append(sector_record)
            track_data = track_data[sector_record.get_size():]

        return Track(mode, cylinder, head, sector_count, sector_size, numbering_map,
                     sector_cylinder_map, sector_head_map, sector_data_records)

    def to_bytes(self) -> bytes:
        """Get this Track's binary representation."""
        head: int = self.head
        if self.sector_head_map is not None:
            head |= 0x40
        if self.sector_cylinder_map is not None:
            head |= 0x80
        sector_size = int(log2(self.sector_size)) - 7

        track_bytes = struct.pack(Track._TRACK_HEADER_FORMAT, self.mode.value, self.cylinder, head,
                                  self.sector_count, sector_size)

        track_bytes += struct.pack(
            Track._SECTOR_MAP_ENTRY_FORMAT.format(sector_count=self.sector_count),
            *self.sector_numbering_map)

        if self.sector_cylinder_map is not None:
            track_bytes += struct.pack(
                Track._SECTOR_MAP_ENTRY_FORMAT.format(sector_count=self.sector_count),
                *self.sector_cylinder_map)

        if self.sector_head_map is not None:
            track_bytes += struct.pack(
                Track._SECTOR_MAP_ENTRY_FORMAT.format(sector_count=self.sector_count),
                *self.sector_head_map)

        for record in self.sector_data_records:
            track_bytes += record.to_bytes()

        return track_bytes


class SectorDataRecordType:
    """Represents the type of a SectorDataRecord."""

    _UNAVAILABLE = 0
    _NORMAL = 1
    _COMPRESSED = 2
    _DELETED_NORMAL = 3
    _DELETED_COMPRESSED = 4
    _ERROR_NORMAL = 5
    _ERROR_COMPRESSED = 6
    _DELETED_ERROR_NORMAL = 7
    _DELETED_ERROR_COMPRESSED = 8

    def __init__(self, record_type_value: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8]) -> None:
        """Initialise the record type."""
        if record_type_value not in (0, 1, 2, 3, 4, 5, 6, 7, 8):
            raise ValueError(f"invalid SectorDataRecordType value of {record_type_value}")

        self._has_data = True
        self._has_error = False
        self._is_compressed = False
        self._is_deleted = False

        if record_type_value == SectorDataRecordType._UNAVAILABLE:
            self._has_data = False

        if record_type_value in (SectorDataRecordType._COMPRESSED,
                                 SectorDataRecordType._DELETED_COMPRESSED,
                                 SectorDataRecordType._ERROR_COMPRESSED,
                                 SectorDataRecordType._DELETED_ERROR_COMPRESSED):
            self._is_compressed = True

        if record_type_value in (SectorDataRecordType._DELETED_NORMAL,
                                 SectorDataRecordType._DELETED_COMPRESSED,
                                 SectorDataRecordType._DELETED_ERROR_NORMAL,
                                 SectorDataRecordType._DELETED_ERROR_COMPRESSED):
            self._is_deleted = True

        if record_type_value in (SectorDataRecordType._ERROR_NORMAL,
                                 SectorDataRecordType._ERROR_COMPRESSED,
                                 SectorDataRecordType._DELETED_ERROR_NORMAL,
                                 SectorDataRecordType._DELETED_ERROR_COMPRESSED):
            self._has_error = True

    @property
    def has_data(self):
        """Get if this record contains data."""
        return self._has_data

    @property
    def is_normal(self):
        """Get if this record has no special attributes."""
        return self._has_data and not self._is_compressed and not self._is_deleted

    @property
    def is_compressed(self):
        """Get if this record is compressed."""
        return self._is_compressed

    @property
    def is_deleted(self):
        """Get if this record is deleted."""
        return self._is_deleted

    @property
    def has_error(self):
        """Get if this record has an error."""
        return self._has_error

    def get_sector_record_size(self, sector_size: int) -> int:
        """Get the of a record with this type in bytes."""
        if self.has_data:
            if self.is_compressed:
                return 1
            return sector_size
        return 0

    def to_value(self) -> int:
        """Get the record type value for this record type."""
        if self.has_data and not self.has_error and not self.is_compressed and not self.is_deleted:
            return SectorDataRecordType._NORMAL

        if self.has_data and self.has_error and not self.is_compressed and not self.is_deleted:
            return SectorDataRecordType._ERROR_NORMAL

        if self.has_data and self.has_error and self.is_compressed and not self.is_deleted:
            return SectorDataRecordType._ERROR_COMPRESSED

        if self.has_data and self.has_error and not self.is_compressed and self.is_deleted:
            return SectorDataRecordType._DELETED_ERROR_NORMAL

        if self.has_data and self.has_error and self.is_compressed and self.is_deleted:
            return SectorDataRecordType._DELETED_ERROR_COMPRESSED

        if self.has_data and not self.has_error and self.is_compressed and not self.is_deleted:
            return SectorDataRecordType._COMPRESSED

        if self.has_data and not self.has_error and self.is_compressed and self.is_deleted:
            return SectorDataRecordType._DELETED_COMPRESSED

        if self.has_data and not self.has_error and not self.is_compressed and self.is_deleted:
            return SectorDataRecordType._DELETED_NORMAL

        return SectorDataRecordType._UNAVAILABLE

    def __repr__(self) -> str:
        """Get a debug representation of this SectorDataRecordType."""
        return (f"SectorDataRecordType(has_data={self.has_data}, is_compressed={self.is_compressed}"
                f", is_deleted={self.is_deleted}, has_error={self.has_error})")


@dataclass
class SectorDataRecord:
    """A record of a sectors data, with some metadata."""

    record_type: SectorDataRecordType
    data: bytes

    _RECORD_TYPE_FORMAT = "B"

    def get_size(self) -> int:
        """Get the size of this SectorDataRecord's binary representation in bytes."""
        return 1 + len(self.data)

    @staticmethod
    def from_bytes(sector_data: bytes, sector_size: SectorSize) -> SectorDataRecord:
        """Create a SectorDataRecord object from it's binary representation."""
        record_type_value, = struct.unpack_from(SectorDataRecord._RECORD_TYPE_FORMAT, sector_data)
        record_type = SectorDataRecordType(record_type_value)

        real_sector_size = record_type.get_sector_record_size(sector_size)
        record_data = sector_data[1:1 + real_sector_size]

        return SectorDataRecord(record_type, record_data)

    def to_bytes(self) -> bytes:
        """Get this SectorDataRecord's binary representation."""
        return bytes([self.record_type.to_value()]) + self.data
