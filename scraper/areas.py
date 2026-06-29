"""Jabodetabek kota/kecamatan reference for building search queries and parsing addresses.

KOTA -> list of kecamatan. Used two ways:
  1. Scraper iterates kecamatan to widen coverage within a kota.
  2. Parser matches address tokens against the known kecamatan set to locate
     the kecamatan, then infers kelurahan (token before) and kota (token after).

Not exhaustive to the kelurahan level (that list is huge and changes); kecamatan
coverage is enough to anchor Indonesian GMaps address parsing reliably.
"""

JABODETABEK = {
    "Jakarta Selatan": [
        "Kebayoran Baru", "Kebayoran Lama", "Pesanggrahan", "Cilandak",
        "Pasar Minggu", "Jagakarsa", "Mampang Prapatan", "Pancoran",
        "Tebet", "Setiabudi",
    ],
    "Jakarta Pusat": [
        "Gambir", "Sawah Besar", "Kemayoran", "Senen", "Cempaka Putih",
        "Menteng", "Tanah Abang", "Johar Baru",
    ],
    "Jakarta Barat": [
        "Kembangan", "Kebon Jeruk", "Palmerah", "Grogol Petamburan",
        "Tambora", "Taman Sari", "Cengkareng", "Kalideres",
    ],
    "Jakarta Timur": [
        "Matraman", "Pulogadung", "Jatinegara", "Cakung", "Duren Sawit",
        "Kramat Jati", "Makasar", "Pasar Rebo", "Ciracas", "Cipayung",
    ],
    "Jakarta Utara": [
        "Penjaringan", "Pademangan", "Tanjung Priok", "Koja",
        "Kelapa Gading", "Cilincing",
    ],
    "Bogor": [
        "Bogor Selatan", "Bogor Timur", "Bogor Utara", "Bogor Tengah",
        "Bogor Barat", "Tanah Sareal",
    ],
    "Depok": [
        "Beji", "Pancoran Mas", "Cipayung", "Sukmajaya", "Cilodong",
        "Cimanggis", "Tapos", "Sawangan", "Bojongsari", "Cinere",
        "Limo", "Pancoran Mas",
    ],
    "Tangerang": [
        "Tangerang", "Karawaci", "Cibodas", "Ciledug", "Cipondoh",
        "Pinang", "Larangan", "Karang Tengah", "Neglasari", "Batuceper",
        "Benda", "Jatiuwung", "Periuk",
    ],
    "Tangerang Selatan": [
        "Serpong", "Serpong Utara", "Pondok Aren", "Ciputat",
        "Ciputat Timur", "Pamulang", "Setu",
    ],
    "Bekasi": [
        "Bekasi Timur", "Bekasi Barat", "Bekasi Selatan", "Bekasi Utara",
        "Medan Satria", "Bantargebang", "Pondok Gede", "Jatiasih",
        "Jatisampurna", "Mustika Jaya", "Rawalumbu", "Pondok Melati",
    ],
}

# Flat, lowercased kecamatan -> kota lookup for the parser.
KECAMATAN_TO_KOTA = {
    kec.lower(): kota
    for kota, kecs in JABODETABEK.items()
    for kec in kecs
}

KOTA_LIST = list(JABODETABEK.keys())


def kecamatan_for(kota: str):
    """Return the kecamatan list for a kota, or [] if unknown."""
    return JABODETABEK.get(kota, [])
