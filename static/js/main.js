/* ====================================================================
   SNYQT Game Hub — 前端交互脚本
   - 主题切换（localStorage 记忆，默认深色）
   - 轮播（自动播放 + 手动导航）
   - 上传表单字段联动 + 封面预览
   - 评论 / 购买 / 下载 AJAX
   - 卡片 staggered 动画
   - toast 通知
   ==================================================================== */
(function () {
    'use strict';

    /* ---------- 主题切换 ---------- */
    function initTheme() {
        var saved = localStorage.getItem('snyqt-theme');
        if (!saved) saved = 'dark'; // 默认深色
        document.documentElement.setAttribute('data-theme', saved);

        var btn = document.querySelector('.theme-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var cur = document.documentElement.getAttribute('data-theme') || 'dark';
            var next = cur === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('snyqt-theme', next);
        });
    }

    /* ---------- 用户下拉菜单 ---------- */
    function initUserMenu() {
        var menu = document.querySelector('.user-menu');
        if (!menu) return;
        var btn = menu.querySelector('.avatar-btn');
        if (!btn) return;
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            menu.classList.toggle('open');
        });
        document.addEventListener('click', function () {
            menu.classList.remove('open');
        });
    }

    /* ---------- 首页轮播 ---------- */
    function initHero() {
        var hero = document.querySelector('.hero');
        if (!hero) return;
        var track = hero.querySelector('.hero-track');
        var slides = hero.querySelectorAll('.hero-slide');
        var dots = hero.querySelectorAll('.hero-dots button');
        var prev = hero.querySelector('.hero-nav .prev');
        var next = hero.querySelector('.hero-nav .next');
        if (!track || !slides.length) return;

        var idx = 0;
        var timer = null;

        function go(i) {
            idx = (i + slides.length) % slides.length;
            track.style.transform = 'translateX(-' + (idx * 100) + '%)';
            dots.forEach(function (d, k) { d.classList.toggle('active', k === idx); });
        }

        function play() {
            stop();
            timer = setInterval(function () { go(idx + 1); }, 5000);
        }
        function stop() { if (timer) clearInterval(timer); }

        if (next) next.addEventListener('click', function () { go(idx + 1); play(); });
        if (prev) prev.addEventListener('click', function () { go(idx - 1); play(); });
        dots.forEach(function (d, k) {
            d.addEventListener('click', function () { go(k); play(); });
        });
        hero.addEventListener('mouseenter', stop);
        hero.addEventListener('mouseleave', play);
        go(0); play();
    }

    /* ---------- 卡片 staggered 动画 ---------- */
    function initStaggered() {
        var cards = document.querySelectorAll('.game-card');
        cards.forEach(function (c, i) {
            c.style.animationDelay = (Math.min(i, 16) * 0.045) + 's';
        });
    }

    /* ---------- 上传表单 ---------- */
    function initUploadForm() {
        var form = document.querySelector('#upload-form');
        if (!form) return;

        var typeSelect = form.querySelector('[name="hosting_type"]');
        var htmlFields = form.querySelector('.html-fields');
        var pyFields = form.querySelector('.python-fields');

        function syncFields() {
            var v = typeSelect ? typeSelect.value : 'html';
            // single_html 和 html 都显示 html-fields，仅 python 隐藏
            if (htmlFields) htmlFields.classList.toggle('hidden', v === 'python');
            if (pyFields) pyFields.classList.toggle('hidden', v !== 'python');
        }
        if (typeSelect) {
            typeSelect.addEventListener('change', syncFields);
            syncFields();
        }

        // 封面预览 + 长宽比校验
        var coverInput = form.querySelector('[name="cover"]');
        if (coverInput) {
            coverInput.addEventListener('change', function () {
                var file = coverInput.files && coverInput.files[0];
                if (!file) return;
                // 读取图片以检测长宽比
                var reader = new FileReader();
                reader.onload = function (ev) {
                    var img = new Image();
                    img.onload = function () {
                        var ratio = img.width / img.height;
                        if (ratio < 0.2 || ratio > 3.5) {
                            // 长宽比超出范围，弹窗裁剪（showCropModal 由 developer.html 定义）
                            if (typeof window.showCropModal === 'function') {
                                window.showCropModal(img, file);
                            } else {
                                // 非开发者页面回退为普通预览
                                showCoverPreview(ev.target.result);
                            }
                        } else {
                            // 正常显示预览
                            showCoverPreview(ev.target.result);
                        }
                    };
                    img.src = ev.target.result;
                };
                reader.readAsDataURL(file);

                // 显示封面预览
                function showCoverPreview(src) {
                    var preview = form.querySelector('.cover-preview');
                    if (preview) {
                        preview.innerHTML = '';
                        var imgEl = document.createElement('img');
                        imgEl.src = src;
                        preview.appendChild(imgEl);
                        preview.classList.remove('hidden');
                    }
                }
            });
        }

        // 截图预览
        var shotsInput = form.querySelector('[name="screenshots"]');
        if (shotsInput) {
            shotsInput.addEventListener('change', function () {
                var preview = form.querySelector('.shots-preview');
                if (!preview) return;
                preview.innerHTML = '';
                if (!shotsInput.files) return;
                Array.prototype.forEach.call(shotsInput.files, function (f) {
                    var img = document.createElement('img');
                    img.src = URL.createObjectURL(f);
                    preview.appendChild(img);
                });
                preview.classList.remove('hidden');
            });
        }

        // 提交（含进度）
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var fd = new FormData(form);
            var progress = form.querySelector('.upload-progress');
            var bar = progress ? progress.querySelector('.bar') : null;
            var submitBtn = form.querySelector('[type="submit"]');
            if (progress) progress.classList.add('show');
            if (bar) bar.style.width = '0%';
            if (submitBtn) submitBtn.disabled = true;

            var xhr = new XMLHttpRequest();
            xhr.open('POST', form.getAttribute('action') || '/api/games/upload');
            // 大文件上传超时设置（10 分钟）
            xhr.timeout = 10 * 60 * 1000;
            xhr.upload.addEventListener('progress', function (e) {
                if (e.lengthComputable && bar) {
                    var pct = Math.round((e.loaded / e.total) * 100);
                    bar.style.width = pct + '%';
                }
            });
            xhr.onload = function () {
                if (submitBtn) submitBtn.disabled = false;
                var data;
                try { data = JSON.parse(xhr.responseText); } catch (_) { data = null; }
                if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
                    toast(data.message || '上传成功', 'success');
                    setTimeout(function () { window.location.reload(); }, 900);
                } else {
                    // 显示后端返回的真实错误信息（如"Python 启动命令不能为空"）
                    var msg = (data && data.message) || '上传失败 (HTTP ' + xhr.status + ')';
                    toast(msg, 'error');
                }
            };
            xhr.onerror = function () {
                if (submitBtn) submitBtn.disabled = false;
                // 网络层错误：连接被中断。显示更详细的诊断信息
                toast('网络连接中断（HTTP ' + xhr.status + '）。可能原因：文件过大、网络波动或服务器重启', 'error');
            };
            xhr.ontimeout = function () {
                if (submitBtn) submitBtn.disabled = false;
                toast('上传超时，请检查网络或减小文件体积', 'error');
            };
            xhr.send(fd);
        });
    }

    /* ---------- 评分输入 ---------- */
    function initStarInput() {
        var wrap = document.querySelector('.stars-input');
        if (!wrap) return;
        var buttons = wrap.querySelectorAll('button');
        var input = document.querySelector('[name="rating-value"]');
        function paint(n) {
            buttons.forEach(function (b, k) {
                b.classList.toggle('on', k < n);
            });
        }
        buttons.forEach(function (b, k) {
            b.addEventListener('click', function () {
                if (input) input.value = (k + 1);
                paint(k + 1);
            });
        });
    }

    /* ---------- 评论提交 ---------- */
    function initReviewForm() {
        var form = document.querySelector('#review-form');
        if (!form) return;
        var gid = form.getAttribute('data-game-id');
        if (!gid) return;

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var rating = (form.querySelector('[name="rating-value"]') || {}).value;
            var comment = (form.querySelector('[name="comment"]') || {}).value || '';
            if (!rating) { toast('请先选择评分', 'warning'); return; }

            var fd = new FormData();
            fd.append('rating', rating);
            fd.append('comment', comment);

            fetch('/api/games/' + gid + '/review', { method: 'POST', body: fd })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message || '评分成功', 'success');
                        setTimeout(function () { window.location.reload(); }, 700);
                    } else {
                        toast(data.message || '评分失败', 'error');
                    }
                })
                .catch(function () { toast('网络错误', 'error'); });
        });
    }

    /* ---------- 购买/获取 ---------- */
    function initPurchaseButtons() {
        document.querySelectorAll('[data-action="purchase"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-game-id');
                if (!gid) return;
                btn.disabled = true;
                fetch('/api/games/' + gid + '/purchase', { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            toast(data.message || '已加入游戏库', 'success');
                            setTimeout(function () { window.location.reload(); }, 800);
                        } else {
                            toast(data.message || '操作失败', 'error');
                            btn.disabled = false;
                        }
                    })
                    .catch(function () {
                        toast('网络错误', 'error');
                        btn.disabled = false;
                    });
            });
        });
    }

    /* ---------- 管理面板操作 ---------- */
    function initAdminActions() {
        // 权限级别修改
        document.querySelectorAll('[data-perm-select]').forEach(function (sel) {
            sel.addEventListener('change', function () {
                var uid = sel.getAttribute('data-user-id');
                var level = sel.value;
                postJSON('/api/admin/permission', { user_id: uid, level: level }, '权限已更新');
            });
        });

        // Python 审核
        document.querySelectorAll('[data-py-review]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var rid = btn.getAttribute('data-py-review');
                var action = btn.getAttribute('data-action');
                var reason = prompt(action === 'reject' ? '拒绝理由（可选）' : '批准说明（可选）', '') || '';
                postJSON('/api/admin/python-review/' + rid, { action: action, reason: reason }, '审核完成');
            });
        });

        // 封禁/解禁
        document.querySelectorAll('[data-ban]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-ban');
                var action = btn.getAttribute('data-action'); // ban / unban
                if (!confirm(action === 'ban' ? '确认封禁该游戏？' : '确认解禁该游戏？')) return;
                fetch('/api/admin/games/' + gid + '/' + action, { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // view 资格
        document.querySelectorAll('[data-view-elig]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-view-elig');
                var action = btn.getAttribute('data-action'); // grant / revoke
                fetch('/api/admin/games/' + gid + '/view-eligibility', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: action })
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // 权限申请审核（批准 / 拒绝）— 按 permissions 表主键 id 审核
        document.querySelectorAll('[data-app-review]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var recordId = btn.getAttribute('data-app-review');
                var action = btn.getAttribute('data-action'); // approve / reject
                if (!confirm(action === 'approve' ? '确认批准该权限申请？' : '确认拒绝该权限申请？')) return;
                postJSON('/api/admin/applications/' + recordId, { action: action, record_id: recordId }, '审核完成');
            });
        });

        /* ---------- 板块折叠 ---------- */
        document.querySelectorAll('[data-admin-block] [data-toggle]').forEach(function (header) {
            header.addEventListener('click', function () {
                var block = header.closest('[data-admin-block]');
                if (block) block.classList.toggle('collapsed');
            });
        });

        // 删除游戏（管理员面板）
        document.querySelectorAll('[data-admin-block] [data-delete]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-delete');
                if (!confirm('确认删除该游戏？\n这将删除游戏记录、评论、文件等所有数据，无法恢复！')) return;
                fetch('/api/admin/games/' + gid, { method: 'DELETE' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // 删除游戏（开发者面板）
        document.querySelectorAll('.dev-games-list [data-delete]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-delete');
                if (!confirm('确认删除该游戏？\n这将删除游戏记录、评论、文件等所有数据，无法恢复！')) return;
                fetch('/api/games/' + gid, { method: 'DELETE' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // 保存入口文件
        document.querySelectorAll('[data-save-entry]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-save-entry');
                var input = btn.closest('.actions').querySelector('.entry-input[data-game-id="' + gid + '"]');
                if (!input) return;
                var entry_file = input.value.trim();
                if (!entry_file) {
                    toast('入口文件不能为空', 'warning');
                    return;
                }
                fetch('/api/games/' + gid + '/entry-file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entry_file: entry_file })
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    toast(data.message || '保存成功', data.success ? 'success' : 'error');
                })
                .catch(function () { toast('网络错误', 'error'); });
            });
        });

        /* ---------- 版本更新：已迁移至开发者面板的推送更新自动填充逻辑 ---------- */
        document.querySelectorAll('[data-admin-block] [data-search-input]').forEach(function (input) {
            input.addEventListener('input', function () {
                var block = input.closest('[data-admin-block]');
                if (!block) return;
                var keyword = input.value.trim().toLowerCase();
                var rows = block.querySelectorAll('[data-search-row]');
                var visibleCount = 0;
                rows.forEach(function (row) {
                    var text = (row.getAttribute('data-search-text') || '').toLowerCase();
                    var match = text.indexOf(keyword) !== -1;
                    row.style.display = match ? '' : 'none';
                    if (match) visibleCount++;
                });
                // 无匹配时显示提示
                if (block.querySelector('.admin-no-match')) {
                    block.classList.toggle('no-match', visibleCount === 0);
                }
            });
            // 阻止输入框点击事件冒泡到折叠头部
            input.addEventListener('click', function (e) { e.stopPropagation(); });
        });
    }

    /* ---------- 权限申请表单 ---------- */
    function initApplyForm() {
        var submitBtn = document.querySelector('[data-apply-submit]');
        if (!submitBtn) return;

        submitBtn.addEventListener('click', function () {
            var selected = document.querySelector('input[name="level"]:checked');
            if (!selected) { toast('请先选择要申请的权限', 'warning'); return; }
            var level = selected.value;
            submitBtn.disabled = true;
            fetch('/api/permissions/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level: level })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message || '申请已提交', 'success');
                        setTimeout(function () { window.location.reload(); }, 900);
                    } else {
                        toast(data.message || '申请失败', 'error');
                        submitBtn.disabled = false;
                    }
                })
                .catch(function () {
                    toast('网络错误', 'error');
                    submitBtn.disabled = false;
                });
        });
    }

    function postJSON(url, body, okMsg) {
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                toast(data.message || okMsg || '操作完成', data.success ? 'success' : 'error');
                if (data.success) setTimeout(function () { window.location.reload(); }, 700);
            })
            .catch(function () { toast('网络错误', 'error'); });
    }

    // 配置变更审核
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function loadConfigReviews() {
        var list = document.getElementById('config-review-list');
        if (!list) return;
        fetch('/api/admin/config-review')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var count = document.getElementById('config-review-count');
                if (count) count.textContent = (data.reviews ? data.reviews.length : 0) + ' items';
                if (!data.reviews || !data.reviews.length) {
                    list.innerHTML = '<div class="empty-state card card-noise"><h3>无待审核配置变更</h3><p>没有需要审核的配置变更</p></div>';
                    return;
                }
                var html = '<div style="overflow-x:auto;"><table class="admin-table"><thead><tr>' +
                    '<th>游戏</th><th>变更项</th><th>原值</th><th>新值</th><th>提交时间</th><th>操作</th></tr></thead><tbody>';
                data.reviews.forEach(function (r) {
                    html += '<tr>' +
                        '<td>' + escapeHtml(r.title) + '</td>' +
                        '<td>' + escapeHtml(r.field_name) + '</td>' +
                        '<td>' + escapeHtml(r.old_value) + '</td>' +
                        '<td style="color:var(--gold-strong);font-weight:600;">' + escapeHtml(r.new_value) + '</td>' +
                        '<td>' + (r.created_at || '') + '</td>' +
                        '<td><div class="flex gap-8">' +
                        '<button class="btn btn-primary btn-sm" data-config-approve="' + r.id + '">批准</button>' +
                        '<button class="btn btn-ghost btn-sm" data-config-reject="' + r.id + '">拒绝</button>' +
                        '</div></td></tr>';
                });
                html += '</tbody></table></div>';
                list.innerHTML = html;
                bindConfigReviewButtons();
            })
            .catch(function () {
                list.innerHTML = '<div class="empty-state card card-noise"><h3>加载失败</h3><p>网络错误</p></div>';
            });
    }

    function bindConfigReviewButtons() {
        document.querySelectorAll('[data-config-approve]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var rid = btn.getAttribute('data-config-approve');
                postJSON('/api/admin/config-review/' + rid, { action: 'approve' }, '已批准');
            });
        });
        document.querySelectorAll('[data-config-reject]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var rid = btn.getAttribute('data-config-reject');
                postJSON('/api/admin/config-review/' + rid, { action: 'reject' }, '已拒绝');
            });
        });
    }

    // 管理员页面初始化加载审核列表
    if (document.getElementById('config-review-block')) {
        loadConfigReviews();
    }

    /* ---------- toast ---------- */
    function toast(msg, type) {
        var container = document.querySelector('.toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container';
            document.body.appendChild(container);
        }
        var el = document.createElement('div');
        el.className = 'toast ' + (type || 'info');
        el.textContent = msg;
        container.appendChild(el);
        // 强制重绘触发动画
        el.offsetWidth;
        el.classList.add('show');
        setTimeout(function () {
            el.classList.remove('show');
            setTimeout(function () { el.remove(); }, 400);
        }, 3200);
    }
    window.snyqtToast = toast;
    // 别名：供开发者面板内联脚本使用
    window.showToast = function (msg, type) { toast(msg, type); };

    /* ---------- 闪光数字滚动（积分） ---------- */
    function initPointsAnim() {
        var chip = document.querySelector('.points-chip');
        if (!chip) return;
        chip.addEventListener('mouseenter', function () {
            chip.style.transform = 'scale(1.06)';
            setTimeout(function () { chip.style.transform = ''; }, 220);
        });
    }

    /* ---------- 合作开发者弹窗 ---------- */
    function initCoDevModal() {
        var modal = document.getElementById('codev-modal');
        if (!modal) return;
        var currentGid = null;

        // 打开弹窗
        document.querySelectorAll('[data-co-dev]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                currentGid = btn.getAttribute('data-co-dev');
                modal.classList.remove('hidden');
                loadCoDevs(currentGid);
            });
        });

        // 关闭弹窗
        var cancelBtn = document.getElementById('codev-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', function () {
                modal.classList.add('hidden');
            });
        }
        modal.addEventListener('click', function (e) {
            if (e.target === modal) modal.classList.add('hidden');
        });

        // 邀请按钮
        var inviteBtn = document.getElementById('codev-invite-btn');
        var usernameInput = document.getElementById('codev-username');
        if (inviteBtn && usernameInput) {
            inviteBtn.addEventListener('click', function () {
                if (!currentGid) return;
                var username = usernameInput.value.trim();
                if (!username) { toast('请输入用户名', 'warning'); return; }
                inviteBtn.disabled = true;
                fetch('/api/games/' + currentGid + '/co-dev', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: username })
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message, 'success');
                        usernameInput.value = '';
                        loadCoDevs(currentGid);
                    } else {
                        toast(data.message || '邀请失败', 'error');
                    }
                    inviteBtn.disabled = false;
                })
                .catch(function () {
                    toast('网络错误', 'error');
                    inviteBtn.disabled = false;
                });
            });
            usernameInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { inviteBtn.click(); }
            });
        }

        function loadCoDevs(gid) {
            var list = document.getElementById('codev-list');
            if (!list) return;
            list.innerHTML = '<div class="text-muted" style="padding:12px;">加载中...</div>';
            fetch('/api/games/' + gid + '/co-dev')
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.success) {
                        list.innerHTML = '<div class="text-muted" style="padding:12px;">加载失败</div>';
                        return;
                    }
                    var coDevs = data.co_devs || [];
                    if (!coDevs.length) {
                        list.innerHTML = '<div class="text-muted" style="padding:12px;">暂无合作开发者</div>';
                        return;
                    }
                    var html = '';
                    coDevs.forEach(function (cd) {
                        var statusLabel = cd.status === 'accepted' ? '已接受' : cd.status === 'pending' ? '待确认' : cd.status;
                        html += '<div class="flex gap-12 items-center justify-between" style="padding:8px 0;border-bottom:1px solid var(--border);">'
                            + '<span><strong>' + (cd.username || '未知用户') + '</strong>'
                            + ' <span class="badge ' + (cd.status === 'accepted' ? 'badge-success' : 'badge-warning') + '" style="font-size:.7rem;">' + statusLabel + '</span></span>'
                            + '<button class="btn btn-danger btn-sm" data-remove-codev="' + cd.user_id + '">移除</button>'
                            + '</div>';
                    });
                    list.innerHTML = html;

                    // 移除按钮事件
                    list.querySelectorAll('[data-remove-codev]').forEach(function (rmBtn) {
                        rmBtn.addEventListener('click', function () {
                            var uid = rmBtn.getAttribute('data-remove-codev');
                            if (!confirm('确认移除该合作开发者？')) return;
                            fetch('/api/games/' + gid + '/co-dev/' + uid, { method: 'DELETE' })
                                .then(function (r) { return r.json(); })
                                .then(function (d) {
                                    toast(d.message || '已移除', d.success ? 'success' : 'error');
                                    if (d.success) loadCoDevs(gid);
                                })
                                .catch(function () { toast('网络错误', 'error'); });
                        });
                    });
                })
                .catch(function () {
                    list.innerHTML = '<div class="text-muted" style="padding:12px;">加载失败</div>';
                });
        }
    }

    /* ---------- 邀请码管理弹窗 ---------- */
    function initInviteModal() {
        var modal = document.getElementById('invite-modal');
        if (!modal) return;
        var currentGid = null;

        // 关闭弹窗
        var cancelBtn = document.getElementById('invite-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', function () {
                modal.classList.add('hidden');
            });
        }
        modal.addEventListener('click', function (e) {
            if (e.target === modal) modal.classList.add('hidden');
        });

        // 生成按钮
        var genBtn = document.getElementById('invite-generate-btn');
        var countInput = document.getElementById('invite-count');
        if (genBtn && countInput) {
            genBtn.addEventListener('click', function () {
                if (!currentGid) return;
                var count = parseInt(countInput.value) || 1;
                genBtn.disabled = true;
                fetch('/api/games/' + currentGid + '/invite-codes', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ count: count })
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message, 'success');
                        loadInviteCodes(currentGid);
                    } else {
                        toast(data.message || '生成失败', 'error');
                    }
                    genBtn.disabled = false;
                })
                .catch(function () {
                    toast('网络错误', 'error');
                    genBtn.disabled = false;
                });
            });
        }

        function loadInviteCodes(gid) {
            var list = document.getElementById('invite-list');
            if (!list) return;
            list.innerHTML = '<div class="text-muted" style="padding:12px;">加载中...</div>';
            fetch('/api/games/' + gid + '/invite-codes')
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.success) {
                        list.innerHTML = '<div class="text-muted" style="padding:12px;">加载失败</div>';
                        return;
                    }
                    var codes = data.codes || [];
                    if (!codes.length) {
                        list.innerHTML = '<div class="text-muted" style="padding:12px;">暂无邀请码</div>';
                        return;
                    }
                    var html = '<div style="max-height:300px;overflow:auto;">';
                    codes.forEach(function (c) {
                        var usedLabel = c.is_used ? '<span class="badge badge-danger" style="font-size:.7rem;">已使用</span>' : '<span class="badge badge-success" style="font-size:.7rem;">未使用</span>';
                        html += '<div class="flex gap-12 items-center justify-between" style="padding:6px 0;border-bottom:1px solid var(--border);">'
                            + '<code style="font-family:monospace;font-size:.9rem;color:var(--gold);">' + c.code + '</code>'
                            + usedLabel
                            + '<span class="text-muted" style="font-size:.7rem;">' + (c.created_at || '') + '</span>'
                            + '</div>';
                    });
                    html += '</div>';
                    list.innerHTML = html;
                })
                .catch(function () {
                    list.innerHTML = '<div class="text-muted" style="padding:12px;">加载失败</div>';
                });
        }

        // 打开弹窗（通过 data-invite-codes 属性）
        document.querySelectorAll('[data-invite-codes]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                currentGid = btn.getAttribute('data-invite-codes');
                modal.classList.remove('hidden');
                loadInviteCodes(currentGid);
            });
        });
    }

    /* ---------- 标签选择器（上传表单） ---------- */
    function initTagSelector() {
        var selector = document.getElementById('tag-selector');
        if (!selector) return;

        var searchInput = document.getElementById('tag-search');
        var dropdown = document.getElementById('tag-dropdown');
        var selectedList = document.getElementById('selected-tags');
        var hiddenInput = document.getElementById('tags-hidden');
        var allTags = [];

        // 加载所有标签
        fetch('/api/tags')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success && data.tags) {
                    allTags = data.tags;
                }
            })
            .catch(function () { /* 静默失败 */ });

        // 已选标签集合 - 使用 Set 并暴露为全局，以便推送更新时外部代码可访问
        window.selectedTags = new Set();

        function renderSelectedTags() {
            selectedList.innerHTML = '';
            var names = [];
            window.selectedTags.forEach(function (n) {
                names.push(n);
                var chip = document.createElement('span');
                chip.className = 'tag-chip';
                chip.textContent = n;
                var xBtn = document.createElement('button');
                xBtn.type = 'button';
                xBtn.className = 'tag-chip-remove';
                xBtn.textContent = '×';
                xBtn.setAttribute('aria-label', '移除标签 ' + n);
                xBtn.addEventListener('click', function (name) {
                    return function () {
                        window.selectedTags.delete(name);
                        renderSelectedTags();
                    };
                }(n));
                chip.appendChild(xBtn);
                selectedList.appendChild(chip);
            });
            hiddenInput.value = names.join(',');
        }
        // 暴露渲染函数供外部调用
        window.renderSelectedTags = renderSelectedTags;

        function filterDropdown() {
            var q = searchInput.value.trim().toLowerCase();
            if (!q) {
                dropdown.style.display = 'none';
                return;
            }
            var filtered = allTags.filter(function (t) {
                return t.name.toLowerCase().indexOf(q) !== -1 && !window.selectedTags.has(t.name);
            });
            if (!filtered.length) {
                dropdown.innerHTML = '<div class="tag-dropdown-item tag-dropdown-empty">无匹配标签，按回车创建</div>';
                dropdown.style.display = 'block';
                return;
            }
            var html = '';
            filtered.forEach(function (t) {
                html += '<div class="tag-dropdown-item" data-tag-name="' + t.name.replace(/"/g, '&quot;') + '">' + t.name + '</div>';
            });
            dropdown.innerHTML = html;
            dropdown.style.display = 'block';

            // 绑定点击事件
            dropdown.querySelectorAll('.tag-dropdown-item').forEach(function (item) {
                item.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    var name = item.getAttribute('data-tag-name');
                    if (name) {
                        window.selectedTags.add(name);
                        renderSelectedTags();
                        searchInput.value = '';
                        dropdown.style.display = 'none';
                    }
                });
            });
        }

        searchInput.addEventListener('input', filterDropdown);
        searchInput.addEventListener('focus', function () {
            if (searchInput.value.trim()) filterDropdown();
        });

        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var name = searchInput.value.trim();
                if (!name) return;
                if (window.selectedTags.has(name)) {
                    searchInput.value = '';
                    dropdown.style.display = 'none';
                    return;
                }
                // 如果标签已存在，直接添加
                var existing = null;
                for (var i = 0; i < allTags.length; i++) {
                    if (allTags[i].name.toLowerCase() === name.toLowerCase()) {
                        existing = allTags[i];
                        break;
                    }
                }
                if (existing) {
                    window.selectedTags.add(existing.name);
                    renderSelectedTags();
                    searchInput.value = '';
                    dropdown.style.display = 'none';
                    return;
                }
                // 创建新标签
                fetch('/api/tags', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name })
                })
                .then(function (r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(function (data) {
                    if (data.success && data.tag) {
                        allTags.push(data.tag);
                        window.selectedTags.add(data.tag.name);
                        renderSelectedTags();
                    } else {
                        console.error('[tag] 创建失败:', data.message || '未知错误');
                    }
                    searchInput.value = '';
                    dropdown.style.display = 'none';
                })
                .catch(function (err) {
                    console.error('[tag] 请求失败:', err);
                    searchInput.value = '';
                    dropdown.style.display = 'none';
                });
            }
        });

        // 点击外部关闭下拉
        document.addEventListener('click', function (e) {
            if (!selector.contains(e.target)) {
                dropdown.style.display = 'none';
            }
        });
    }

    /* ---------- 首页标签筛选栏 ---------- */
    function initTagFilters() {
        var container = document.getElementById('tag-filters');
        if (!container) return;

        fetch('/api/tags')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success || !data.tags || !data.tags.length) {
                    container.style.display = 'none';
                    return;
                }
                var params = new URLSearchParams(window.location.search);
                var activeTag = params.get('tag') || '';
                var html = '';
                data.tags.forEach(function (t) {
                    var isActive = (activeTag === t.name);
                    var url = '?tag=' + encodeURIComponent(t.name);
                    html += '<a href="' + url + '" class="tag-filter-chip' + (isActive ? ' active' : '') + '">' + t.name + '</a>';
                });
                container.innerHTML = html;
            })
            .catch(function () {
                container.style.display = 'none';
            });
    }

    /* ---------- 入口 ---------- */
    document.addEventListener('DOMContentLoaded', function () {
        // 每个 init 包裹 try-catch，防止单个失败阻塞后续初始化
        var fns = [initTheme, initUserMenu, initHero, initStaggered, initUploadForm,
            initStarInput, initReviewForm, initPurchaseButtons, initAdminActions,
            initApplyForm, initPointsAnim, initCoDevModal, initInviteModal,
            initTagSelector, initTagFilters];
        fns.forEach(function (fn) {
            try { fn(); } catch (e) { console.error('[init] ' + fn.name + ' 失败:', e); }
        });
    });
})();
