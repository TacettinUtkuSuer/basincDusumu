# basincDusumu

Basınç düşümü / kayıp katsayısı (Lc) hesaplama programı (`basinc_dusumu.pyw`, Tkinter + matplotlib).

## Kurulum ve çalıştırma

```
pip install numpy matplotlib
```

Windows'ta `basinc_dusumu.pyw` dosyasına çift tıklayın (konsol penceresi açılmaz).
Diğer sistemlerde: `python basinc_dusumu.pyw`.

Tüm veriler (akışkanlar, eğriler, tüm sekmelerdeki girişler, açılan CSV) programın yanındaki
`basinc_dusumu_data.json` dosyasına **otomatik kaydedilir** ve program açılınca otomatik yüklenir.
Klasör yazılabilir değilse dosya kullanıcı ana klasörüne yazılır (Dosya → Veri dosyasının konumunu göster).

## Sekmeler

1. **Akışkanlar** – Akışkan ekle / kopyala / yeniden adlandır / sil. Sıcaklığa bağlı
   ρ [kg/m³], cp [J/kg·K], μ [Pa·s], k [W/m·K] tablosu (hücreye çift tıklayarak düzenleme,
   Excel'den yapıştırma, CSV al/ver). Özelliklerin grafikleri ve girilen sıcaklıkta
   interpolasyonla değerler (ν ve Pr dahil).
2. **Tek Nokta Hesabı** – Akışkan-1, debi (kg/s, kg/h, g/s, LPM, m³/h, m³/s), T1, dP, Dh1 →
   Re ve Lc = dP/(½ρv²). Akışkan-2 (T2 boşsa T1, Dh2 boşsa Dh1) için:
   - **a)** aynı Re (dinamik benzerlik, aynı Lc) noktasındaki ṁ2, V̇2 ve dP2,
   - **b)** aynı dP ve sabit Lc varsayımıyla ṁ2, V̇2 ve Re2.
3. **CSV → Re-Lc** – Akışkan, Dh (zorunlu) seçilir, ölçüm CSV'si açılır. Ayraç / ondalık /
   başlık / atlanacak satır ayarlanabilir; sıcaklık, dP ve debi sütunları ile birimleri seçilir
   (sıcaklık sütunu yoksa sabit değer verilebilir). Sonuç Re–Lc tablosu CSV'ye kaydedilebilir,
   grafiklerle gösterilir ve Sekme 4 için "eğri" olarak saklanabilir.
4. **dP vs Debi** – Kayıtlı (veya CSV'den yüklenen) Re–Lc eğrisi, akışkan, Dh, sıcaklık aralığı /
   adımı ve debi aralığı seçilir; her sıcaklık için dP–debi eğrileri çizilir, tablo CSV'ye kaydedilir.
   Model: log-log interpolasyon, güç yasası (Lc = a·Re^b) veya log-log 2. derece polinom.
   Eğrinin Re aralığı dışındaki kısımlar kesikli çizilir.

Formüller: A = π·Dh²/4, v = ṁ/(ρ·A), Re = ρ·v·Dh/μ, Lc = dP/(½·ρ·v²).
Akışkan özellikleri tablodaki noktalar arasında doğrusal interpolasyonla bulunur; tablo dışında uç değer
kullanılır ve uyarı verilir.

CSV dışa aktarım biçimi (`,` / `.` veya Excel TR için `;` / `,`) Ayarlar menüsünden seçilir.
