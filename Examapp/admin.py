from django.contrib import admin
from django.contrib.auth.models import User
from .models import UserRole, Exam, Question, Choice, StudentExam, Answer, PasswordResetOTP, StudentProxy, TeacherProxy


# ── Student Admin ─────────────────────────────────────────────────────────────

@admin.register(StudentProxy)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('get_reg_number', 'get_full_name', 'email', 'is_active', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'email')
    list_filter = ('is_active',)
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'last_login')
    fields = ('username', 'first_name', 'last_name', 'email', 'is_active', 'date_joined', 'last_login')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(userrole__role='student')

    @admin.display(description='Registration Number')
    def get_reg_number(self, obj):
        return obj.username

    @admin.display(description='Full Name')
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or '—'


# ── Teacher Admin ─────────────────────────────────────────────────────────────


@admin.register(TeacherProxy)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ('username', 'get_full_name', 'email', 'is_active', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'email')
    list_filter = ('is_active',)
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'last_login')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(userrole__role='teacher')

    @admin.display(description='Full Name')
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or '—'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        UserRole.objects.get_or_create(user=obj, defaults={'role': 'teacher'})

    def get_fields(self, request, obj=None):
        if obj is None:
            # Creating a new teacher
            return ('username', 'first_name', 'last_name', 'email', 'password', 'is_active')
        return ('username', 'first_name', 'last_name', 'email', 'is_active', 'date_joined', 'last_login')

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ('date_joined', 'last_login')

    def save_model(self, request, obj, form, change):
        if not change:
            # New teacher — hash the password
            raw_password = form.data.get('password')
            if raw_password:
                obj.set_password(raw_password)
        super().save_model(request, obj, form, change)
        UserRole.objects.get_or_create(user=obj, defaults={'role': 'teacher'})





class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 4


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1
    show_change_link = True




@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ('title', 'teacher', 'duration_minutes', 'total_marks', 'passing_marks', 'is_published', 'start_time', 'end_time')
    list_filter = ('is_published', 'teacher')
    search_fields = ('title', 'teacher__username')
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('question_text', 'exam', 'question_type', 'marks', 'order')
    list_filter = ('question_type', 'exam')
    search_fields = ('question_text',)
    inlines = [ChoiceInline]


@admin.register(StudentExam)
class StudentExamAdmin(admin.ModelAdmin):
    list_display = ('student', 'exam', 'is_submitted', 'obtained_marks', 'tab_switch_count', 'total_suspicion_warnings')
    list_filter = ('is_submitted', 'exam')
    search_fields = ('student__username', 'exam__title')


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ('student_exam', 'question', 'selected_choice', 'is_correct', 'marks_obtained')
    list_filter = ('is_correct',)


admin.site.register(Choice)

# Hide the default User section — Students and Teachers have their own sections
admin.site.unregister(User)

# UserRole is managed automatically; hide from admin to avoid confusion
# (kept only for superuser debugging if needed)

@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp', 'created_at', 'is_used')
    list_filter = ('is_used',)
    search_fields = ('user__username',)
    readonly_fields = ('otp', 'created_at')

